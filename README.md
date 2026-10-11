# FisioGame - Dashboard

Dashboard em **Python + Streamlit** que monitora o ESP32 do projeto FisioGame (labirinto controlado por joystick com MPU6050 e 2 servomotores). Os dados chegam por **Bluetooth Classic (SPP)**.

Código do ESP32: repositório `esp-labirinto` (pasta `FisioGame_BT/`).

## Como rodar

```bash
pip install -r requirements.txt
streamlit run app.py
```

Abre em http://localhost:8501.

## Conectando ao ESP32 (Windows)

1. Grave o `FisioGame_BT.ino` no ESP32 (ESP32 clássico, ex.: DevKit/WROOM).
2. No Windows: **Configurações → Bluetooth → Adicionar dispositivo** e pareie **FisioGame-ESP32**.
3. Em **Mais opções de Bluetooth → Portas COM** aparecem duas portas; a de **saída** (Outgoing) é a correta. Se uma não funcionar, teste a outra.
4. No dashboard: fonte **ESP32 via Bluetooth**, escolha a porta COM, clique **Conectar**.

Linux: `sudo rfcomm bind 0 <MAC_DO_ESP32>` e use `/dev/rfcomm0`.

## Formato dos dados

O ESP32 envia 1 linha JSON a cada 200 ms:

```json
{"t":12345,"roll":12.5,"pitch":-1.0,"ax":0.1,"ay":2.0,"az":9.5,"temp":27.0,"servo_inf":102,"servo_sup":90,"estado":"ATIVO"}
```

Estados: `NORMAL` (joystick parado, servos em 90°), `ATIVO` (joystick inclinado, servos atuando), `LIMITE` (servo no fim de curso: alerta).

## O que o dashboard mostra

- Estado do sistema e alertas (normal / ativo / limite / conexão perdida)
- Inclinação (roll/pitch), ângulo dos servos, aceleração e temperatura
- Status da conexão (taxa de amostras, última amostra, arquivo de log)
- Eventos de mudança de estado
- Histórico da sessão (download em CSV) e log automático em `data/`
- **Registro de testes**: botão que grava a leitura atual com o cenário e o resultado observado em `data/registro_testes.csv`

## Modo simulação

Existe apenas para desenvolver o dashboard sem o hardware. Ele fica **sempre identificado** na tela e na coluna `fonte` dos CSVs (`simulacao` x `esp32`). Não use dados simulados como evidência de teste.

## Estrutura

```
app.py              # dashboard (leitura serial/BT + interface)
requirements.txt
data/               # logs CSV gerados em execução
.streamlit/config.toml
```
