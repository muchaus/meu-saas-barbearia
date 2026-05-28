from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import mercadopago
import re
import unicodedata
import random

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MODELOS DE DADOS ---
class DadosAgendamento(BaseModel):
    id_salao: int
    nome_cliente: str
    whatsapp: str
    data_hora: str
    id_servico: int

class DadosCadastro(BaseModel):
    nome_salao: str
    telefone: str
    mp_access_token: str

# --- FUNÇÃO AUXILIAR ---
def gerar_slug(texto: str) -> str:
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8')
    texto = texto.lower()
    texto = re.sub(r'[^a-z0-9]+', '-', texto)
    return texto.strip('-')

# --- 1. ROTA DE CADASTRO DE NOVOS SALÕES (NOVA) ---
@app.post("/cadastrar-estabelecimento")
def cadastrar_estabelecimento(dados: DadosCadastro):
    slug_gerado = gerar_slug(dados.nome_salao)
    
    try:
        conexao = sqlite3.connect("banco_saas.db")
        cursor = conexao.cursor()
        
        cursor.execute("SELECT id FROM Saloes WHERE slug = ?", (slug_gerado,))
        se_existe = cursor.fetchone()
        
        if se_existe:
            slug_gerado = f"{slug_gerado}-{random.randint(100, 999)}"
            
        cursor.execute(
            "INSERT INTO Saloes (nome_salao, slug, telefone, mp_access_token) VALUES (?, ?, ?, ?)", 
            (dados.nome_salao, slug_gerado, dados.telefone, dados.mp_access_token)
        )
        conexao.commit()
        conexao.close()
        
        return {
            "sucesso": True, 
            "slug": slug_gerado, 
            "link_do_cliente": f"https://meu-saas-barbearia.vercel.app/?salao={slug_gerado}"
        }
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

# --- 2. ROTA DE CANCELAMENTO ---
@app.post("/cancelar-agendamento/{id_agendamento}")
async def cancelar_agendamento(id_agendamento: int):
    try:
        conexao = sqlite3.connect("banco_saas.db")
        cursor = conexao.cursor()
        cursor.execute("UPDATE Agendamentos SET ativo = 0 WHERE id = ?", (id_agendamento,))
        conexao.commit()
        conexao.close()
        return {"status": "sucesso"}
    except Exception as e:
        return {"status": "erro", "detalhe": str(e)}

# --- 3. ROTA DE LISTAR SALÕES ---
@app.get("/saloes")
def listar_saloes():
    conexao = sqlite3.connect("banco_saas.db")
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()
    cursor.execute("SELECT * FROM Saloes")
    saloes_encontrados = cursor.fetchall()
    conexao.close()
    return {"saloes": [dict(salao) for salao in saloes_encontrados]}

# --- 4. ROTA DE VER SALÃO E SERVIÇOS ---
@app.get("/salao/{slug}")
def ver_salao(slug: str):
    conexao = sqlite3.connect("banco_saas.db")
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()
    cursor.execute("SELECT id, nome_salao, telefone FROM Saloes WHERE slug = ?", (slug,))
    salao = cursor.fetchone()
    if salao:
        salao_dict = dict(salao)
        cursor.execute("SELECT id, nome_servico, preco, tempo_duracao FROM Servicos WHERE id_salao = ?", (salao_dict["id"],))
        servicos = cursor.fetchall()
        salao_dict["servicos"] = [dict(s) for s in servicos]
        conexao.close()
        return salao_dict
    conexao.close()
    return {"erro": "Salão não encontrado"}

# --- 5. ROTA DE HORÁRIOS DISPONÍVEIS ---
@app.get("/horarios/{id_salao}/{data_escolhida}")
def ver_horarios(id_salao: int, data_escolhida: str):
    conexao = sqlite3.connect("banco_saas.db")
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()
    texto_busca = f"{data_escolhida}%"
    cursor.execute("SELECT data_hora FROM Agendamentos WHERE id_salao = ? AND data_hora LIKE ? AND ativo = 1", (id_salao, texto_busca))
    agendamentos_marcados = cursor.fetchall()
    horarios_ocupados = [ag["data_hora"].split(" ")[1] for ag in agendamentos_marcados]
    conexao.close()
    todos_horarios = ["09:00", "10:00", "11:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00"]
    horarios_livres = [h for h in todos_horarios if h not in horarios_ocupados]
    return {"data": data_escolhida, "horarios_disponiveis": horarios_livres}

# --- 6. ROTA DE GERAR PIX ---
@app.post("/gerar-pix")
def gerar_pix(dados: DadosAgendamento):
    conexao = sqlite3.connect("banco_saas.db")
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()
    cursor.execute("SELECT mp_access_token FROM Saloes WHERE id = ?", (dados.id_salao,))
    salao = cursor.fetchone()
    cursor.execute("SELECT preco, nome_servico FROM Servicos WHERE id = ?", (dados.id_servico,))
    servico = cursor.fetchone()
    
    sdk = mercadopago.SDK(salao["mp_access_token"])
    payment_data = {
        "transaction_amount": float(servico["preco"]),
        "description": f"Agendamento: {servico['nome_servico']} - {dados.data_hora}",
        "payment_method_id": "pix",
        "notification_url": "https://powdered-unworried-superior.ngrok-free.dev/webhook/mercado-pago",
        "payer": {"email": "cliente@teste.com", "first_name": dados.nome_cliente}
    }
    
    resposta_mp = sdk.payment().create(payment_data)
    pagamento = resposta_mp["response"]
    
    if pagamento.get("status") == "pending":
        cursor.execute(
            "INSERT INTO Agendamentos (id_salao, nome_cliente, whatsapp_cliente, data_hora, status, id_pagamento_mp, ativo) VALUES (?, ?, ?, ?, 'Pendente', ?, 1)", 
            (dados.id_salao, dados.nome_cliente, dados.whatsapp, dados.data_hora, pagamento["id"])
        )
        conexao.commit()
        conexao.close()
        return {
            "sucesso": True, 
            "copia_cola": pagamento["point_of_interaction"]["transaction_data"]["qr_code"], 
            "qr_code_base64": pagamento["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        }
    
    conexao.close()
    return {"sucesso": False, "erro": "Falha"}

# --- 7. ROTA DE LISTAR AGENDAMENTOS (DASHBOARD) ---
@app.get("/admin/agendamentos/{id_salao}")
def listar_agendamentos(id_salao: int):
    conexao = sqlite3.connect("banco_saas.db")
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()
    cursor.execute("SELECT id, nome_cliente, whatsapp_cliente, data_hora, status FROM Agendamentos WHERE id_salao = ? AND ativo = 1 ORDER BY data_hora ASC", (id_salao,))
    agendamentos = cursor.fetchall()
    conexao.close()
    return {"agendamentos": [dict(ag) for ag in agendamentos]}

# --- 8. ROTA DE MARCAR COMO PAGO (MANUAL) ---
@app.put("/admin/agendamentos/{id_agendamento}/pago")
def marcar_como_pago(id_agendamento: int):
    conexao = sqlite3.connect("banco_saas.db")
    cursor = conexao.cursor()
    cursor.execute("UPDATE Agendamentos SET status = 'Pago' WHERE id = ?", (id_agendamento,))
    conexao.commit()
    conexao.close()
    return {"sucesso": True}

# --- 9. WEBHOOK MERCADO PAGO ---
@app.post("/webhook/mercado-pago")
async def webhook_mp(request: Request):
    dados = await request.json()
    # Mantenha sua lógica original de tratamento do webhook aqui
    return {"status": "ok"}