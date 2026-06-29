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
    from duckduckgo_search import DDGS
except ImportError:
    pass

# ======= CONFIGURAÇÕES GERAIS =======
CANDIDATO_NOME = "PEDRO MOURA NERES DE CARVALHO"
DB_PATH = os.path.join(os.path.dirname(__file__), 'alertas_nuvem.db')

# Configurações do Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8837567755:AAFtRHLRgsMT2FOOD2lPa8NZ7PKbFlIJZ9c")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8506704219")
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
                    excertos = g.get('excerpts', [])
                    trecho = excertos[0] if excertos else f"Data: {g.get('date', '')}"
                    alertas.append({
                        "categoria": "Alertas Pessoais (Seu Nome)",
                        "id": g_id, "fonte": "Querido Diário (Município: " + g.get('territory_name', '') + ")",
                        "url": g.get('url', ''), "info": f"Trecho: {trecho[:300]}..."
                    })
                    
        # Busca 2: Editais Municipais da Área Jurídica
        params_edital = {"querystring": '"concurso público" "edital" "procurador"', "published_since": since_date.isoformat(), "size": 10}
        response_edital = requests.get(url, params=params_edital, timeout=20)
        if response_edital.status_code == 200:
            for g in response_edital.json().get('gazettes', []):
                uf = g.get('territory_id', '')[0:2]
                if uf in ['31', '32', '33', '35', '50', '51', '52', '53']:
                    g_id = "qd_edital_" + g.get('id', g.get('url'))
                    if not is_processed(g_id):
                        excertos = g.get('excerpts', [])
                        trecho = excertos[0] if excertos else f"Data: {g.get('date', '')}"
                        alertas.append({
                            "categoria": "Radar de Novos Editais",
                            "id": g_id, "fonte": "Querido Diário (Edital Mun: " + g.get('territory_name', '') + ")",
                            "url": g.get('url', ''), "info": f"Trecho do Edital: {trecho[:300]}..."
                        })
    except Exception as e:
        print(f"Erro Querido Diário: {e}")
    return alertas

def search_datajud():
    alertas = []
    tribunais = ["trf4", "trf6", "tse", "tre-go", "tre-df"]
    headers = {"Authorization": f"APIKey {DATAJUD_API_KEY}", "Content-Type": "application/json"}
    payload = {"query": {"match_phrase": {"partes.nome": CANDIDATO_NOME}}, "size": 10}
    limite_data = datetime.datetime.now() - datetime.timedelta(days=30) # Corta processos muito antigos (retroativos)
    
    for tribunal in tribunais:
        url = f"https://api-publica.datajud.cnj.jus.br/api_publica_{tribunal}/_search"
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                hits = response.json().get('hits', {}).get('hits', [])
                for hit in hits:
                    source = hit.get('_source', {})
                    num_processo = source.get('numeroProcesso', 'N/A')
                    data_ajuizamento_str = source.get('dataAjuizamento', '')
                    
                    # Filtra apenas processos recentes para evitar avalanche de processos antigos de anos atrás
                    if data_ajuizamento_str:
                        try:
                            # Tenta parsear a data (Ex: 2026-06-25T14:30:00.000Z)
                            data_ajuizamento = datetime.datetime.fromisoformat(data_ajuizamento_str.replace('Z', '+00:00')).replace(tzinfo=None)
                            if data_ajuizamento < limite_data:
                                continue # Ignora processos velhos
                        except:
                            pass
                            
                    alerta_id = f"datajud_{tribunal}_{num_processo}"
                    if not is_processed(alerta_id):
                        classe = source.get('classe', {}).get('nome', 'N/A')
                        assuntos = [a.get('nome', '') for a in source.get('assuntos', [])]
                        assunto_str = ", ".join(assuntos)[:150]
                        alertas.append({
                            "categoria": "Alertas Pessoais (Seu Nome)",
                            "id": alerta_id, "fonte": f"DataJud ({tribunal.upper()})",
                            "url": f"Processo Nº {num_processo}", "info": f"Classe: {classe} | Assuntos: {assunto_str} | Data: {data_ajuizamento_str[:10]}"
                        })
        except Exception as e:
            print(f"Erro DataJud {tribunal}: {e}")
    return alertas

def perform_ddg_search(query, label, categoria):
    alertas = []
    try:
        resultados = DDGS().text(query, max_results=3, timelimit='d') # Limitado a resultados detectados nos ultimos dias
        for r in resultados:
            url = r.get('href')
            alerta_id = f"ddg_{label}_{url}"
            if not is_processed(alerta_id):
                alertas.append({
                    "categoria": categoria,
                    "id": alerta_id, "fonte": f"Web/DOU ({label})",
                    "url": url, "info": f"{r.get('title')}\nTrecho: {r.get('body')}"
                })
        time.sleep(2)
    except Exception as e:
        print(f"Erro DuckDuckGo ({label}): {e}")
    return alertas

def search_web_and_dou():
    alertas = []
    try:
        # 1. Pessoal (Geral + DOU)
        alertas.extend(perform_ddg_search(f'"{CANDIDATO_NOME}"', "Menção Geral/DOU", "Alertas Pessoais (Seu Nome)"))
        
        # 2. Convocações (Bancas + DOU)
        for alvo in CONVOCACOES_ALVO:
            cargo_clean = alvo["cargo"].replace('"', '')
            q = f'"{alvo["orgao"]}" convocação {cargo_clean} (site:cebraspe.org.br OR site:fgv.br OR site:concursosfcc.com.br OR site:in.gov.br)'
            alertas.extend(perform_ddg_search(q, f"Fila na Banca/DOU: {alvo['orgao']}", "Acompanhamento de Filas/Convocações"))
            
        # 3. Novos Editais Regionais (Centro-Oeste e Sudeste - APENAS ÁREA JURÍDICA + DOU Nacional)
        q_reg = '"edital de abertura" "concurso público" ("procurador" OR "defensor" OR "juiz" OR "promotor" OR "advogado" OR "analista judiciário") (GO OR MT OR MS OR DF OR SP OR RJ OR MG OR ES OR site:in.gov.br)'
        alertas.extend(perform_ddg_search(q_reg, "Novos Editais (Jurídicos SE/CO/DOU)", "Radar de Novos Editais"))
    except Exception as e:
        print(f"Erro Geração DDG/DOU: {e}")
    return alertas

# ======= ENVIO DE TELEGRAM =======
def send_telegram(novos_alertas):
    if not novos_alertas:
        return
        
    categorias = {"Alertas Pessoais (Seu Nome)": [], "Acompanhamento de Filas/Convocações": [], "Radar de Novos Editais": []}
    for a in novos_alertas:
        if a['categoria'] in categorias:
            categorias[a['categoria']].append(a)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    def send_chunk(text_chunk):
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text_chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code != 200:
                print(f"Erro Telegram: {resp.text}")
        except Exception as e:
            print(f"Erro conexão Telegram: {e}")

    msg_atual = f"🚨 <b>ALERTA DO ROBÔ DE CONCURSOS</b>\n\nForam detectados <b>{len(novos_alertas)}</b> novos registros!\n\n"
    
    for cat_name, cat_alertas in categorias.items():
        if cat_alertas:
            titulo_cat = f"📌 <b>{cat_name}</b>\n"
            if len(msg_atual) + len(titulo_cat) > 3500:
                send_chunk(msg_atual)
                msg_atual = ""
            msg_atual += titulo_cat
            
            for alerta in cat_alertas:
                # Remove tags HTML acidentais do texto extraido para nao quebrar o layout do telegram
                info_text = alerta['info'].replace("<", "").replace(">", "").strip()
                fonte_text = alerta['fonte'].replace("<", "").replace(">", "").strip()
                
                # Deixa a URL crua para o telegram converter automaticamente em link clicavel, evitando problemas com href tag
                bloco_alerta = f"▪️ <b>{fonte_text}</b>\nDetalhes: <i>{info_text}</i>\n🔗 Link: {alerta['url']}\n\n"
                
                if len(msg_atual) + len(bloco_alerta) > 3500:
                    send_chunk(msg_atual)
                    msg_atual = ""
                    
                msg_atual += bloco_alerta

    if msg_atual.strip():
        msg_atual += "🤖 <i>Gerado automaticamente pelo Antigravity Cloud Monitor</i>"
        if len(msg_atual) > 3500:
            send_chunk(msg_atual[:3500])
        else:
            send_chunk(msg_atual)

# ======= LOOP PRINCIPAL =======
def main():
    print(f"Iniciando varredura na nuvem em {datetime.datetime.now()}")
    init_db()
    
    alertas = []
    alertas.extend(search_queridodiario())
    alertas.extend(search_datajud())
    alertas.extend(search_web_and_dou())
    
    if alertas:
        print(f"Encontrados {len(alertas)} novos alertas. Enviando Telegram...")
        send_telegram(alertas)
        for a in alertas:
            mark_processed(a['id'], a['fonte'], a['url'])
    else:
        print("Nenhum alerta novo. O e-mail não será enviado.")

if __name__ == "__main__":
    main()
