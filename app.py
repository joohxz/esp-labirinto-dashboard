"""
FisioGame - Dashboard de monitoramento do ESP32 (Streamlit)

O ESP32 envia uma linha JSON a cada ~200 ms por Bluetooth Classic (SPP).
No PC, depois de parear o "FisioGame-ESP32", o Windows cria uma porta COM;
este dashboard le essa porta com pyserial.

Para desenvolver sem o hardware existe o modo "Simulação". Ele fica sempre
identificado na tela e nos arquivos CSV (coluna `fonte`), para nunca ser
confundido com dados reais do ESP32.

Executar:  streamlit run app.py
"""

import json
import math
import random
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pyserial nao instalado
    serial = None
    list_ports = None

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# Mesmos valores do firmware (FisioGame_BT.ino)
SERVO_PISO, SERVO_TETO, SERVO_MEIO = 50, 120, 90
ZONA_MORTA = 5.0
TIMEOUT_SEM_DADOS_S = 3.0

COLUNAS = ["hora", "fonte", "roll", "pitch", "ax", "ay", "az", "temp",
           "servo_inf", "servo_sup", "estado"]


class Telemetria:
    """Recebe amostras (serial ou simulacao) em uma thread e guarda o historico."""

    def __init__(self):
        self.lock = threading.Lock()
        self.dados = deque(maxlen=5000)
        self.eventos = deque(maxlen=200)
        self.fonte = "nenhuma"
        self.erro = ""
        self.ultimo_rx = None
        self.total = 0
        self._stop = threading.Event()
        self._thread = None
        self._csv = None
        self._estado_anterior = None

    # ---- controle ----
    def parar(self):
        self._stop.set()
        self.fonte = "nenhuma"

    def iniciar(self, fonte, porta=None, baud=115200):
        self.parar()
        self._stop = threading.Event()
        with self.lock:
            self.dados.clear()
            self.eventos.clear()
            self.total = 0
            self.ultimo_rx = None
            self._estado_anterior = None
        self.erro = ""
        self.fonte = fonte
        self._csv = DATA_DIR / f"log_{fonte}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        alvo = self._loop_serial if fonte == "esp32" else self._loop_simulacao
        args = (self._stop, porta, baud) if fonte == "esp32" else (self._stop,)
        self._thread = threading.Thread(target=alvo, args=args, daemon=True)
        self._thread.start()

    # ---- entrada de dados ----
    def _processar_linha(self, linha, fonte):
        linha = linha.strip()
        if not linha.startswith("{"):
            return
        try:
            bruto = json.loads(linha)
            amostra = {
                "hora": datetime.now(),
                "fonte": fonte,
                "roll": float(bruto["roll"]),
                "pitch": float(bruto["pitch"]),
                "ax": float(bruto.get("ax", 0)),
                "ay": float(bruto.get("ay", 0)),
                "az": float(bruto.get("az", 0)),
                "temp": float(bruto.get("temp", 0)),
                "servo_inf": int(bruto["servo_inf"]),
                "servo_sup": int(bruto["servo_sup"]),
                "estado": str(bruto["estado"]),
            }
        except (ValueError, KeyError, TypeError):
            return  # linha quebrada (comum no inicio da conexao): ignora
        self._registrar(amostra)

    def _registrar(self, amostra):
        with self.lock:
            self.dados.append(amostra)
            self.total += 1
            self.ultimo_rx = time.time()
            if amostra["estado"] != self._estado_anterior:
                self.eventos.appendleft({
                    "hora": amostra["hora"].strftime("%H:%M:%S"),
                    "de": self._estado_anterior or "-",
                    "para": amostra["estado"],
                    "roll": amostra["roll"],
                    "pitch": amostra["pitch"],
                    "fonte": amostra["fonte"],
                })
                self._estado_anterior = amostra["estado"]
        # log em disco
        novo = not self._csv.exists()
        with open(self._csv, "a", encoding="utf-8") as f:
            if novo:
                f.write(",".join(COLUNAS) + "\n")
            f.write(",".join(
                amostra["hora"].isoformat(timespec="milliseconds") if c == "hora" else str(amostra[c])
                for c in COLUNAS) + "\n")

    def _loop_serial(self, stop, porta, baud):
        if serial is None:
            self.erro = "pyserial nao instalado (pip install pyserial)"
            return
        try:
            with serial.Serial(porta, baud, timeout=1) as s:
                while not stop.is_set():
                    linha = s.readline().decode("utf-8", "ignore")
                    if linha:
                        self._processar_linha(linha, "esp32")
        except Exception as e:  # porta ocupada, ESP desligado, etc.
            self.erro = f"{type(e).__name__}: {e}"

    def _loop_simulacao(self, stop):
        """Gera um ciclo de 40 s: parado -> inclina -> limite -> volta."""
        t0 = time.time()
        while not stop.is_set():
            t = (time.time() - t0) % 40
            if t < 10:
                roll = 0.0
            elif t < 20:
                roll = (t - 10) * 2.0          # 0 -> 20 graus
            elif t < 27:
                roll = 20 + (t - 20) * 5.0      # 20 -> 55 graus
            elif t < 35:
                roll = 55 - (t - 27) * 6.875    # volta a 0
            else:
                roll = 0.0
            roll += random.gauss(0, 0.6)
            pitch = random.gauss(0, 0.6)
            amostra = amostra_do_modelo(roll, pitch)
            self._registrar(amostra)
            time.sleep(0.2)

    # ---- saida ----
    def dataframe(self):
        with self.lock:
            df = pd.DataFrame(list(self.dados), columns=COLUNAS)
        return df

    def lista_eventos(self):
        with self.lock:
            return pd.DataFrame(list(self.eventos))


def amostra_do_modelo(roll, pitch):
    """Mesma regra do firmware, usada apenas na simulacao."""
    r = 0 if abs(roll) < ZONA_MORTA else roll
    p = 0 if abs(pitch) < ZONA_MORTA else pitch
    inf = round(SERVO_MEIO + r)
    sup = round(SERVO_MEIO + p)
    no_limite = inf <= SERVO_PISO or inf >= SERVO_TETO or sup <= SERVO_PISO or sup >= SERVO_TETO
    inf = max(SERVO_PISO, min(SERVO_TETO, inf))
    sup = max(SERVO_PISO, min(SERVO_TETO, sup))
    estado = "LIMITE" if no_limite else ("ATIVO" if (r or p) else "NORMAL")
    rr, pr = math.radians(roll), math.radians(pitch)
    return {
        "hora": datetime.now(), "fonte": "simulacao",
        "roll": round(roll, 1), "pitch": round(pitch, 1),
        "ax": round(-9.81 * math.sin(pr), 2),
        "ay": round(9.81 * math.sin(rr) * math.cos(pr), 2),
        "az": round(9.81 * math.cos(rr) * math.cos(pr), 2),
        "temp": round(27 + random.gauss(0, 0.1), 1),
        "servo_inf": inf, "servo_sup": sup, "estado": estado,
    }


@st.cache_resource
def get_store():
    return Telemetria()


# ======================= Interface =======================
st.set_page_config(page_title="FisioGame - Dashboard", page_icon="🎮", layout="wide")
store = get_store()

st.title("🎮 FisioGame - Monitoramento do ESP32")

with st.sidebar:
    st.header("Conexão")
    fonte_ui = st.radio("Fonte de dados", ["ESP32 via Bluetooth (porta COM)", "Simulação (demonstração)"])
    if fonte_ui.startswith("ESP32"):
        portas = []
        if list_ports is not None:
            portas = [f"{p.device} - {p.description}" for p in list_ports.comports()]
        if portas:
            escolha = st.selectbox("Porta serial / Bluetooth", portas)
        else:
            escolha = st.text_input("Porta (ex.: COM5 ou /dev/rfcomm0)")
        baud = st.number_input("Baud rate", value=115200, step=1200)
        c1, c2 = st.columns(2)
        if c1.button("Conectar", type="primary") and escolha:
            store.iniciar("esp32", escolha.split(" - ")[0].strip(), int(baud))
        if c2.button("Desconectar"):
            store.parar()
        st.caption("Pareie primeiro o dispositivo **FisioGame-ESP32** nas configurações Bluetooth do PC.")
    else:
        if st.button("Iniciar simulação", type="primary"):
            store.iniciar("simulacao")
        if st.button("Parar"):
            store.parar()
        st.caption("Dados gerados por software. Não representam leituras do ESP32.")
    janela = st.slider("Amostras exibidas nos gráficos", 50, 1500, 300, step=50)


@st.fragment(run_every=1.0)
def painel(janela):
    df = store.dataframe()
    sem_dados = df.empty
    atraso = (time.time() - store.ultimo_rx) if store.ultimo_rx else None

    if store.erro:
        st.error(f"Erro de conexão: {store.erro}")
    if store.fonte == "simulacao":
        st.warning("MODO SIMULAÇÃO - os dados abaixo são gerados por software, não vêm do ESP32.")
    elif store.fonte == "esp32" and not sem_dados and atraso is not None and atraso <= TIMEOUT_SEM_DADOS_S:
        st.success("AO VIVO - dados recebidos do ESP32 via Bluetooth.")
    elif store.fonte == "esp32" and not sem_dados:
        st.error(f"Conexão perdida: nenhum pacote há {atraso:.0f} s.")
    elif store.fonte == "esp32":
        st.info("Conectando... aguardando o primeiro pacote do ESP32.")
    else:
        st.info("Sem fonte de dados ativa. Escolha uma opção na barra lateral e clique em Conectar/Iniciar.")

    if sem_dados:
        return

    ult = df.iloc[-1]
    cor = {"NORMAL": "🟢", "ATIVO": "🟡", "LIMITE": "🔴"}.get(ult["estado"], "⚪")
    m = st.columns(6)
    m[0].metric(f"Estado {cor}", ult["estado"])
    m[1].metric("Roll (°)", f"{ult['roll']:.1f}")
    m[2].metric("Pitch (°)", f"{ult['pitch']:.1f}")
    m[3].metric("Servo inferior (°)", int(ult["servo_inf"]))
    m[4].metric("Servo superior (°)", int(ult["servo_sup"]))
    m[5].metric("Temperatura (°C)", f"{ult['temp']:.1f}")

    if ult["estado"] == "LIMITE":
        st.error("ALERTA: inclinação no limite mecânico dos servos.")
    elif ult["estado"] == "ATIVO":
        st.warning("Joystick inclinado: servos atuando.")
    else:
        st.success("Condição normal: joystick parado, servos no ponto médio.")

    d = df.tail(janela).copy()
    d["tempo (s)"] = (d["hora"] - d["hora"].iloc[0]).dt.total_seconds().round(1)
    d = d.set_index("tempo (s)")
    g1, g2 = st.columns(2)
    with g1:
        st.subheader("Inclinação do joystick (°)")
        st.line_chart(d[["roll", "pitch"]])
    with g2:
        st.subheader("Ângulo dos servomotores (°)")
        st.line_chart(d[["servo_inf", "servo_sup"]])
    g3, g4 = st.columns(2)
    with g3:
        st.subheader("Aceleração (m/s²)")
        st.line_chart(d[["ax", "ay", "az"]])
    with g4:
        st.subheader("Status do sistema")
        taxa = len(df[df["hora"] > df["hora"].iloc[-1] - pd.Timedelta(seconds=10)]) / 10
        st.write(f"- Fonte: **{ult['fonte']}**")
        st.write(f"- Amostras recebidas: **{store.total}**")
        st.write(f"- Taxa: **{taxa:.1f} amostras/s**")
        st.write(f"- Última amostra: **{ult['hora']:%H:%M:%S}**")
        st.write(f"- Arquivo de log: `{store._csv.name if store._csv else '-'}`")
        st.subheader("Eventos (mudança de estado)")
        ev = store.lista_eventos()
        if not ev.empty:
            st.dataframe(ev.head(8), hide_index=True)


painel(janela)

# ---------- Registro de testes (fora do fragmento, para nao reiniciar a digitacao) ----------
st.divider()
st.header("Registro de testes")
st.caption("Clique em registrar durante o teste. Cada linha guarda a leitura exibida no momento "
           "(com a fonte: esp32 ou simulacao).")

CENARIOS = {
    "Teste 01 - Condição normal": "Joystick parado -> estado NORMAL, servos em 90°.",
    "Teste 02 - Condição de alerta": "Joystick inclinado -> estado ATIVO/LIMITE, servos fora de 90°, dashboard atualiza.",
}
ARQ_TESTES = DATA_DIR / "registro_testes.csv"
cen = st.selectbox("Cenário", list(CENARIOS))
st.write(f"**Resultado esperado:** {CENARIOS[cen]}")
col_a, col_b = st.columns([1, 3])
passou = col_a.radio("Resultado observado", ["Passou", "Falhou"], horizontal=True)
obs = col_b.text_input("Observações")
if st.button("Registrar leitura atual"):
    df_now = store.dataframe()
    if df_now.empty:
        st.error("Sem dados no momento. Conecte o ESP32 antes de registrar.")
    else:
        u = df_now.iloc[-1]
        linha = pd.DataFrame([{
            "registro": datetime.now().isoformat(timespec="seconds"), "cenario": cen,
            "fonte": u["fonte"], "roll": u["roll"], "pitch": u["pitch"],
            "servo_inf": u["servo_inf"], "servo_sup": u["servo_sup"], "estado": u["estado"],
            "resultado": passou, "observacoes": obs,
        }])
        linha.to_csv(ARQ_TESTES, mode="a", header=not ARQ_TESTES.exists(), index=False)
        st.success("Leitura registrada.")
if ARQ_TESTES.exists():
    reg = pd.read_csv(ARQ_TESTES)
    st.dataframe(reg, hide_index=True)
    st.download_button("Baixar registro de testes (CSV)", reg.to_csv(index=False).encode("utf-8"),
                       "registro_testes.csv", "text/csv")

with st.expander("Histórico completo da sessão"):
    hist = store.dataframe()
    st.dataframe(hist.tail(500), hide_index=True)
    if not hist.empty:
        st.download_button("Baixar histórico (CSV)", hist.to_csv(index=False).encode("utf-8"),
                           "historico_sessao.csv", "text/csv")
