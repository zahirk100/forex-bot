# MT5 Auto Trading Bot (Forex) + Web Dashboard

## Belangrijk (super duidelijk)
**GitHub is alleen opslag van code.**
Je kunt **niet** "MT5 downloaden in GitHub" en daar trades laten draaien.

Je hebt nodig:
1. **Een machine (meestal Windows VPS)** waar je MT5 terminal installeert en inlogt.
2. Daarop draai je deze Python app.
3. GitHub gebruik je alleen om code te bewaren/updaten.

---

## Architectuur in 1 zin
- **GitHub** = code repo
- **Windows VPS met MT5** = runtime waar trades echt worden geplaatst
- **Dashboard (`/dashboard`)** = beheer (start/stop/status)

---

## Stap-voor-stap: werkend krijgen

### Stap 1 — VPS voorbereiden
- Neem een Windows VPS.
- Installeer MetaTrader 5.
- Log in op je broker account in MT5.
- Zet AutoTrading aan in MT5.

### Stap 2 — Code ophalen vanaf GitHub op je VPS
```bash
git clone <jouw-repo-url>
cd <repo-map>
```

### Stap 3 — Python dependencies installeren
```bash
pip install -r requirements.txt
```

### Stap 4 — Config invullen
```bash
copy .env.example .env
```
Vul in `.env` je waarden in, minimaal:
- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`
- `MT5_SYMBOL`

### Stap 5 — MT5 check doen
```bash
python live_check.py --symbol EURUSD
```
Als deze faalt, gaat live trading nog niet werken.

### Stap 6 — Web backend starten
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```
Open daarna:
- `http://<vps-ip>:8000/dashboard`
- `http://<vps-ip>:8000/mt5/status`

### Stap 7 — Bot starten vanuit dashboard/API
- Dashboard: klik **Start**
- Of API: `POST /mt5/start`

---

## Wat draait waar?
- Trades worden geplaatst door `mt5_bot.py` via MetaTrader5 Python API.
- Dit werkt alleen als de app draait op een systeem met lokale MT5 terminal sessie.
- Daarom: **niet op GitHub zelf**, maar op je VPS/server.

---

## Veilig live starten
1. Eerst demo 2-4 weken.
2. Dan live met klein risico (0.25%-0.5% per trade).
3. Gebruik altijd stop-loss en daily loss limiet.

Als je wilt, volgende stap: ik maak een exact **Windows VPS install script/checklist** voor jouw broker zodat je het in 30-45 min live test-klaar hebt.


---

## 7) Gratis VPS opties (realistisch)
Per april 2026 geldt grofweg:
- **Oracle Cloud Always Free:** gratis compute is vooral Linux (Oracle Linux/Ubuntu), niet ideaal voor native Windows+MT5 setup.
- **Azure Free Account:** tijdelijke gratis VM-capaciteit (meestal 12 maanden, met limieten).
- **AWS Free Tier / credits:** tijdelijke credits/free-tier, geen onbeperkt gratis Windows VPS.

Praktisch advies voor MT5:
- Voor stabiel 24/7 MT5 live gebruik kies meestal een **goedkope betaalde Windows VPS**.
- Gratis tiers zijn handig voor testen, maar vaak beperkt in duur/capaciteit/region availability.
