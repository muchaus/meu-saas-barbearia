from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import mercadopago

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DadosAgendamento(BaseModel):
    id_salao: int
    nome_cliente: str
    whatsapp: str
    data_hora: str
    id_servico: int

@app.get("/saloes")
def listar_saloes():
    conexao = sqlite3.connect("banco_saas.db")
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()
    cursor.execute("SELECT * FROM Saloes")
    saloes_encontrados = cursor.fetchall()
    conexao.close()
    return {"saloes": [dict(salao) for salao in saloes_encontrados]}

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

@app.get("/horarios/{id_salao}/{data_escolhida}")
def ver_horarios(id_salao: int, data_escolhida: str):
    conexao = sqlite3.connect("banco_saas.db")
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()
    texto_busca = f"{data_escolhida}%"
    cursor.execute(
        "SELECT data_hora FROM Agendamentos WHERE id_salao = ? AND data_hora LIKE ? AND ativo = 1",
        (id_salao, texto_busca)
    )
    agendamentos_marcados = cursor.fetchall()
    horarios_ocupados = [ag["data_hora"].split(" ")[1] for ag in agendamentos_marcados]
    conexao.close()
    todos_horarios = ["09:00", "10:00", "11:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00"]
    horarios_livres = [h for h in todos_horarios if h not in horarios_ocupados]
    return {"data": data_escolhida, "horarios_disponiveis": horarios_livres}

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
        "payer": {
            "email": "cliente@teste.com",
            "first_name": dados.nome_cliente
        }
    }
    resposta_mp = sdk.payment().create(payment_data)
    pagamento = resposta_mp["response"]
    if pagamento.get("status") == "pending":
        codigo_copia_cola = pagamento["point_of_interaction"]["transaction_data"]["qr_code"]
        link_qr_code = pagamento["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        id_pagamento_mp = pagamento["id"]
        cursor.execute("""
            INSERT INTO Agendamentos (id_salao, nome_cliente, whatsapp_cliente, data_hora, status, id_pagamento_mp, ativo)
            VALUES (?, ?, ?, ?, 'Pendente', ?, 1)
        """, (dados.id_salao, dados.nome_cliente, dados.whatsapp, dados.data_hora, id_pagamento_mp))
        conexao.commit()
        conexao.close()
        return {
            "sucesso": True,
            "copia_cola": codigo_copia_cola,
            "qr_code_base64": link_qr_code
        }
    conexao.close()
    return {"sucesso": False, "erro": "Falha ao gerar o PIX"}

@app.get("/admin/agendamentos/{id_salao}")
def listar_agendamentos(id_salao: int):
    conexao = sqlite3.connect("banco_saas.db")
    conexao.row_factory = sqlite3.Row
    cursor = conexao.cursor()
    cursor.execute("""
        SELECT id, nome_cliente, whatsapp_cliente, data_hora, status 
        FROM Agendamentos 
        WHERE id_salao = ? AND ativo = 1
        ORDER BY data_hora ASC
    """, (id_salao,))
    agendamentos = cursor.fetchall()
    conexao.close()
    return {"agendamentos": [dict(ag) for ag in agendamentos]}

@app.put("/admin/agendamentos/{id_agendamento}/pago")
def marcar_como_pago(id_agendamento: int):
    conexao = sqlite3.connect("banco_saas.db")
    cursor = conexao.cursor()
    cursor.execute("UPDATE Agendamentos SET status = 'Pago' WHERE id = ?", (id_agendamento,))
    conexao.commit()
    conexao.close()
    return {"sucesso": True, "mensagem": "Status atualizado"}

# ==========================================
# ROTA CANCELAMENTO
# ==========================================
@app.post("/cancelar-agendamento/{id_agendamento}")
def cancelar_agendamento(id_agendamento: int):
    try:
        conexao = sqlite3.connect("banco_saas.db")
        cursor = conexao.cursor()
        cursor.execute("UPDATE Agendamentos SET ativo = 0 WHERE id = ?", (id_agendamento,))
        conexao.commit()
        conexao.close()
        return {"status": "sucesso"}
    except Exception as e:
        return {"status": "erro", "detalhe": str(e)}

@app.post("/webhook/mercado-pago")
async def webhook_mp(request: Request):
    dados = await request.json()
    print("🔔 CHEGOU UM AVISO DO MERCADO PAGO:", dados)
    if dados.get("type") == "payment" or dados.get("action") == "payment.updated":
        id_pagamento_mp = str(dados.get("data", {}).get("id"))
        if id_pagamento_mp and id_pagamento_mp != "None":
            conexao = sqlite3.connect("banco_saas.db")
            conexao.row_factory = sqlite3.Row
            cursor = conexao.cursor()
            cursor.execute("SELECT id_salao FROM Agendamentos WHERE id_pagamento_mp = ?", (id_pagamento_mp,))
            agendamento = cursor.fetchone()
            if agendamento:
                cursor.execute("SELECT mp_access_token FROM Saloes WHERE id = ?", (agendamento["id_salao"],))
                salao = cursor.fetchone()
                sdk = mercadopago.SDK(salao["mp_access_token"])
                resposta = sdk.payment().get(id_pagamento_mp)
                pagamento_real = resposta["response"]
                if pagamento_real.get("status") == "approved":
                    cursor.execute("UPDATE Agendamentos SET status = 'Pago' WHERE id_pagamento_mp = ?", (id_pagamento_mp,))
                    conexao.commit()
                    print(f"✅ SUCESSO: Pagamento {id_pagamento_mp} APROVADO!")
                else:
                    print(f"⏳ Pagamento {id_pagamento_mp} ainda: {pagamento_real.get('status')}")
            conexao.close()
    return {"status": "ok"}