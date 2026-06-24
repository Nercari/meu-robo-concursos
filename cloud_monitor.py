import os
import json
import sqlite3
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# As we are deploying to cloud, we rely on standard libraries plus the ones in requirements.txt
import requests
try:
    import cloudscraper
except ImportError:
    pass

try:
    from duckduckgo_search import DDGS
except ImportError:
    pass

# ======= CONFIGURAÇÕES GERAIS =======
CANDIDATO_NOME = "PEDRO MOURA NERES DE CARVALHO"
DB_PATH = os.path.join(os.path.dirname(__file__), 'alertas_nuvem.db')

# Lendo variáveis de ambiente configuradas na Nuvem
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "pedromneresc@outlook.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "FYxVfC%AbfDFr7$R8kdRqMC#@T9Vn@rw9rgZkwsUmvM83462^r")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "pedromneresc@outlook.com") # Enviando de você para você mesmo
DATAJUD_API_KEY = os.getenv("DATAJUD_API_KEY", "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw==")

# ======= BANCO DE DADOS =======
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS alertas
                 (id TEXT PRIMARY KEY, fonte TEXT, url TEXT, data TEXT)''')
    conn.commit()
    conn.close()

def is_processed(alerta_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM alertas WHERE id=?", (alerta_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_processed(alerta_id, fonte, url):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    hoje = datetime.date.today().isoformat()
    c.execute("INSERT OR IGNORE INTO alertas (id, fonte, url, data) VALUES (?, ?, ?, ?)",
              (alerta_id, fonte, url, hoje))
    conn.commit()
    conn.close()

# ======= MÓDULOS DE BUSCA =======

def search_queridodiario():
    alertas = []
    url = "https://api.queridodiario.ok.org.br/api/gazettes/"
    hoje = datetime.date.today()
    since_date = hoje - datetime.timedelta(days=2)
    params = {
        "querystring": f'"{CANDIDATO_NOME}"',
        "published_since": since_date.isoformat(),
        "size": 50
    }
    try:
        response = requests.get(url, params=params, timeout=20)
        if response.status_code == 200:
            for g in response.json().get('gazettes', []):
                g_id = "qd_" + g.get('id', g.get('url'))
                if not is_processed(g_id):
                    alertas.append({
                        "id": g_id,
                        "fonte": "Querido Diário (Município: " + g.get('territory_name', '') + ")",
                        "url": g.get('url', ''),
                        "info": f"Data: {g.get('date', '')}"
                    })
    except Exception as e:
        print(f"Erro Querido Diário: {e}")
    return alertas

def search_datajud():
    alertas = []
    tribunais = ["trf4", "trf6", "tse", "tre-go", "tre-df"]
    headers = {"Authorization": f"APIKey {DATAJUD_API_KEY}", "Content-Type": "application/json"}
    payload = {"query": {"match_phrase": {"partes.nome": CANDIDATO_NOME}}, "size": 10}
    
    for tribunal in tribunais:
        url = f"https://api-publica.datajud.cnj.jus.br/api_publica_{tribunal}/_search"
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                hits = response.json().get('hits', {}).get('hits', [])
                for hit in hits:
                    source = hit.get('_source', {})
                    num_processo = source.get('numeroProcesso', 'N/A')
                    alerta_id = f"datajud_{tribunal}_{num_processo}"
                    if not is_processed(alerta_id):
                        alertas.append({
                            "id": alerta_id,
                            "fonte": f"DataJud ({tribunal.upper()})",
                            "url": f"Processo Nº {num_processo}",
                            "info": f"Autuação: {source.get('dataAjuizamento', 'N/A')}"
                        })
        except Exception as e:
            print(f"Erro DataJud {tribunal}: {e}")
    return alertas

def search_dou_cloudscraper():
    alertas = []
    try:
        scraper = cloudscraper.create_scraper()
        query = CANDIDATO_NOME.replace(" ", "+")
        url = f"https://www.in.gov.br/consulta/-/buscar/dou?q=%22{query}%22&s=todos&exactDate=all&sortType=0"
        response = scraper.get(url, timeout=30)
        if response.status_code == 200:
            if "Nenhum resultado" not in response.text and "0 Resultados" not in response.text:
                # Há possibilidade de resultados! Como não podemos parsear os links detalhados 
                # perfeitamente sem BS4 complexo, avisamos para ele olhar o link geral:
                alerta_id = f"dou_{datetime.date.today().isoformat()}"
                if not is_processed(alerta_id):
                    alertas.append({
                        "id": alerta_id,
                        "fonte": "DOU - Diário Oficial da União",
                        "url": url,
                        "info": "Um novo registro foi detectado na busca oficial do DOU hoje."
                    })
    except Exception as e:
        print(f"Erro DOU Cloudscraper: {e}")
    return alertas

def search_duckduckgo_web():
    alertas = []
    try:
        # Busca menções em sites de bancas ou domínios governamentais (exclui in.gov.br para evitar duplo)
        query = f'"{CANDIDATO_NOME}" (site:cebraspe.org.br OR site:jus.br OR site:gov.br) -site:in.gov.br'
        resultados = DDGS().text(query, max_results=5)
        for r in resultados:
            url = r.get('href')
            alerta_id = f"ddg_{url}"
            if not is_processed(alerta_id):
                alertas.append({
                    "id": alerta_id,
                    "fonte": "Busca Web (DuckDuckGo)",
                    "url": url,
                    "info": f"Encontrado em: {r.get('title')}\nTrecho: {r.get('body')}"
                })
    except Exception as e:
        print(f"Erro DuckDuckGo: {e}")
    return alertas

# ======= ENVIO DE EMAIL =======
def send_email(novos_alertas):
    if not novos_alertas:
        return
        
    if not EMAIL_PASSWORD:
        print("Senha de App do Email não configurada. Imprimindo alertas no console:")
        for a in novos_alertas:
            print(a)
        return

    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"🚨 ALERTA DE CONCURSO: Novo Registro para {CANDIDATO_NOME}"
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <h2>Alerta do Robô de Concursos</h2>
        <p>Olá! Encontrei <b>{len(novos_alertas)}</b> novos registros envolvendo o nome <strong>{CANDIDATO_NOME}</strong>.</p>
        <hr>
    """
    
    for alerta in novos_alertas:
        html += f"""
        <div style="margin-bottom: 20px; padding: 15px; border-left: 5px solid #005A9C; background-color: #f9f9f9;">
            <h3 style="margin-top: 0; color: #005A9C;">{alerta['fonte']}</h3>
            <p><strong>URL/Processo:</strong> <a href="{alerta['url']}">{alerta['url']}</a></p>
            <p><strong>Detalhes:</strong> {alerta['info']}</p>
        </div>
        """
        
    html += """
        <hr>
        <p style="font-size: 12px; color: #888;">Este e-mail foi gerado automaticamente pelo seu robô de automação na nuvem.</p>
      </body>
    </html>
    """
    
    msg.attach(MIMEText(html, 'html'))
    
    try:
        # Usando o servidor do Outlook/Hotmail em vez do Gmail
        server = smtplib.SMTP('smtp-mail.outlook.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"E-mail de alerta enviado com sucesso para {EMAIL_RECEIVER}.")
        
        # Só marca como processado se o email for enviado com sucesso
        for alerta in novos_alertas:
            mark_processed(alerta['id'], alerta['fonte'], alerta['url'])
            
    except Exception as e:
        print(f"Falha ao enviar e-mail: {e}")

# ======= MAIN =======
def main():
    init_db()
    todos_alertas = []
    
    print("Iniciando varredura na nuvem...")
    todos_alertas.extend(search_queridodiario())
    todos_alertas.extend(search_datajud())
    todos_alertas.extend(search_dou_cloudscraper())
    todos_alertas.extend(search_duckduckgo_web())
    
    print(f"Varredura concluída. {len(todos_alertas)} novos alertas detectados.")
    if todos_alertas:
        send_email(todos_alertas)

if __name__ == "__main__":
    main()
