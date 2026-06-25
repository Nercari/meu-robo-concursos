import os
import json
import sqlite3
import datetime
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

EMAIL_SENDER = os.getenv("EMAIL_SENDER", "pedromneresc@outlook.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "FYxVfC%AbfDFr7$R8kdRqMC#@T9Vn@rw9rgZkwsUmvM83462^r")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "pedromneresc@outlook.com")
DATAJUD_API_KEY = os.getenv("DATAJUD_API_KEY", "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw==")

# ======= REGRAS DE NEGÓCIO =======
CONVOCACOES_ALVO = [
    {"orgao": "Tribunal Regional Federal da 4ª Região", "cargo": '"técnico judiciário" "área administrativa"'},
    {"orgao": "Tribunal Regional Federal da 6ª Região", "cargo": 'oeste "analista judiciário" "área judiciária"'},
    {"orgao": "conselho federal de psicologia", "cargo": '"analista técnico de licitações e contratos"'},
    {"orgao": "tribunal regional eleitoral de goiás", "cargo": '"analista judiciário"'},
    {"orgao": "tribunal regional eleitoral do distrito federal", "cargo": '"técnico judiciário" "área judiciária"'},
]

# ======= BANCO DE DADOS =======
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS alertas (id TEXT PRIMARY KEY, fonte TEXT, url TEXT, data TEXT)''')
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
    c.execute("INSERT OR IGNORE INTO alertas (id, fonte, url, data) VALUES (?, ?, ?, ?)", (alerta_id, fonte, url, hoje))
    conn.commit()
    conn.close()

# ======= MÓDULOS DE BUSCA =======
def search_queridodiario():
    alertas = []
    url = "https://api.queridodiario.ok.org.br/api/gazettes/"
    hoje = datetime.date.today()
    since_date = hoje - datetime.timedelta(days=2)
    params = {"querystring": f'"{CANDIDATO_NOME}"', "published_since": since_date.isoformat(), "size": 50}
    try:
        # Busca 1: Nome Pessoal
        response = requests.get(url, params=params, timeout=20)
        if response.status_code == 200:
            for g in response.json().get('gazettes', []):
                g_id = "qd_nome_" + g.get('id', g.get('url'))
                if not is_processed(g_id):
                    alertas.append({
                        "categoria": "Alertas Pessoais (Seu Nome)",
                        "id": g_id, "fonte": "Querido Diário (Município: " + g.get('territory_name', '') + ")",
                        "url": g.get('url', ''), "info": f"Data: {g.get('date', '')}"
                    })
                    
        # Busca 2: Editais Municipais da Área Jurídica
        # Nota: O QD não aceita OR complexo facilmente na API pública, vamos usar "procurador" e "concurso público"
        params_edital = {"querystring": '"concurso público" "edital" "procurador"', "published_since": since_date.isoformat(), "size": 10}
        response_edital = requests.get(url, params=params_edital, timeout=20)
        if response_edital.status_code == 200:
            for g in response_edital.json().get('gazettes', []):
                # Filtra SE/CO pelas siglas dos estados retornados
                uf = g.get('territory_id', '')[0:2] # Pega o estado do código IBGE do território (Ex: 35 é SP)
                # Aceita códigos IBGE do SE/CO (31 MG, 32 ES, 33 RJ, 35 SP, 50 MS, 51 MT, 52 GO, 53 DF)
                if uf in ['31', '32', '33', '35', '50', '51', '52', '53']:
                    g_id = "qd_edital_" + g.get('id', g.get('url'))
                    if not is_processed(g_id):
                        alertas.append({
                            "categoria": "Radar de Novos Editais",
                            "id": g_id, "fonte": "Querido Diário (Edital Mun: " + g.get('territory_name', '') + ")",
                            "url": g.get('url', ''), "info": f"Novo edital possivelmente jurídico. Data: {g.get('date', '')}"
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
                            "categoria": "Alertas Pessoais (Seu Nome)",
                            "id": alerta_id, "fonte": f"DataJud ({tribunal.upper()})",
                            "url": f"Processo Nº {num_processo}", "info": f"Autuação: {source.get('dataAjuizamento', 'N/A')}"
                        })
        except Exception as e:
            print(f"Erro DataJud {tribunal}: {e}")
    return alertas

def perform_dou_search(scraper, query, label, categoria):
    alertas = []
    try:
        q_encoded = query.replace(" ", "+")
        url = f"https://www.in.gov.br/consulta/-/buscar/dou?q={q_encoded}&s=todos&exactDate=all&sortType=0"
        response = scraper.get(url, timeout=30)
        if response.status_code == 200 and "Nenhum resultado" not in response.text and "0 Resultados" not in response.text:
            alerta_id = f"dou_{label}_{datetime.date.today().isoformat()}"
            if not is_processed(alerta_id):
                alertas.append({
                    "categoria": categoria,
                    "id": alerta_id, "fonte": f"DOU - {label}",
                    "url": url, "info": "Registro detectado no DOU oficial hoje para este termo."
                })
        time.sleep(2) # Evitar block
    except Exception as e:
        print(f"Erro DOU ({label}): {e}")
    return alertas

def search_dou_cloudscraper():
    alertas = []
    try:
        scraper = cloudscraper.create_scraper()
        # 1. Pessoal
        alertas.extend(perform_dou_search(scraper, f'"{CANDIDATO_NOME}"', "Seu Nome", "Alertas Pessoais (Seu Nome)"))
        
        # 2. Convocações
        for alvo in CONVOCACOES_ALVO:
            q = f'"{alvo["orgao"]}" "convocação" {alvo["cargo"]}'
            alertas.extend(perform_dou_search(scraper, q, f"Fila: {alvo['orgao']}", "Acompanhamento de Filas/Convocações"))
            
        # 3. Novos Editais (Cargos Jurídicos Federais)
        q_editais = '"edital de abertura" "concurso público" ("procurador" OR "defensor" OR "juiz" OR "promotor" OR "advogado da união" OR "analista judiciário")'
        alertas.extend(perform_dou_search(scraper, q_editais, "Novos Editais (Jurídicos Federais)", "Radar de Novos Editais"))
    except Exception as e:
        print(f"Erro Geração Scraper DOU: {e}")
    return alertas

def perform_ddg_search(query, label, categoria):
    alertas = []
    try:
        resultados = DDGS().text(query, max_results=3)
        for r in resultados:
            url = r.get('href')
            alerta_id = f"ddg_{label}_{url}"
            if not is_processed(alerta_id):
                alertas.append({
                    "categoria": categoria,
                    "id": alerta_id, "fonte": f"Web ({label})",
                    "url": url, "info": f"Encontrado em: {r.get('title')}\nTrecho: {r.get('body')}"
                })
        time.sleep(2)
    except Exception as e:
        print(f"Erro DuckDuckGo ({label}): {e}")
    return alertas

def search_duckduckgo_web():
    alertas = []
    try:
        # 1. Pessoal
        alertas.extend(perform_ddg_search(f'"{CANDIDATO_NOME}"', "Menção Web", "Alertas Pessoais (Seu Nome)"))
        
        # 2. Convocações (buscando nas bancas)
        for alvo in CONVOCACOES_ALVO:
            cargo_clean = alvo["cargo"].replace('"', '')
            q = f'"{alvo["orgao"]}" convocação {cargo_clean} (site:cebraspe.org.br OR site:fgv.br OR site:concursosfcc.com.br)'
            alertas.extend(perform_ddg_search(q, f"Fila na Banca: {alvo['orgao']}", "Acompanhamento de Filas/Convocações"))
            
        # 3. Novos Editais Regionais (Centro-Oeste e Sudeste - APENAS ÁREA JURÍDICA)
        q_reg = '"edital de abertura" "concurso público" ("procurador" OR "defensor" OR "juiz" OR "promotor" OR "advogado" OR "analista judiciário") (GO OR MT OR MS OR DF OR SP OR RJ OR MG OR ES)'
        alertas.extend(perform_ddg_search(q_reg, "Novos Editais (Jurídicos SE/CO)", "Radar de Novos Editais"))
    except Exception as e:
        print(f"Erro Geração DDG: {e}")
    return alertas

# ======= ENVIO DE EMAIL =======
def send_email(novos_alertas):
    if not novos_alertas:
        return
        
    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"🚨 ALERTA: Concursos e Monitoramento"
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER

    # Agrupar alertas por categoria
    categorias = {"Alertas Pessoais (Seu Nome)": [], "Acompanhamento de Filas/Convocações": [], "Radar de Novos Editais": []}
    for a in novos_alertas:
        if a['categoria'] in categorias:
            categorias[a['categoria']].append(a)

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <h2>Relatório do Robô de Concursos</h2>
        <p>Foram detectados <b>{len(novos_alertas)}</b> novos registros nas suas regras de monitoramento.</p>
    """
    
    for cat_name, cat_alertas in categorias.items():
        if cat_alertas:
            html += f"<hr><h3 style='color: #D32F2F;'>📌 {cat_name}</h3>"
            for alerta in cat_alertas:
                html += f"""
                <div style="margin-bottom: 20px; padding: 15px; border-left: 5px solid #005A9C; background-color: #f9f9f9;">
                    <h4 style="margin-top: 0; color: #005A9C;">{alerta['fonte']}</h4>
                    <p><strong>Acesso:</strong> <a href="{alerta['url']}">{alerta['url']}</a></p>
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
        server = smtplib.SMTP('smtp-mail.outlook.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"E-mail de alerta enviado com sucesso para {EMAIL_RECEIVER}.")
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")

# ======= LOOP PRINCIPAL =======
def main():
    print(f"Iniciando varredura na nuvem em {datetime.datetime.now()}")
    init_db()
    
    alertas = []
    alertas.extend(search_queridodiario())
    alertas.extend(search_datajud())
    alertas.extend(search_dou_cloudscraper())
    alertas.extend(search_duckduckgo_web())
    
    if alertas:
        print(f"Encontrados {len(alertas)} novos alertas. Enviando e-mail...")
        send_email(alertas)
        for a in alertas:
            mark_processed(a['id'], a['fonte'], a['url'])
    else:
        print("Nenhum alerta novo. O e-mail não será enviado.")

if __name__ == "__main__":
    main()
