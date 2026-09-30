# TimeEdit – automatisk grupprumsbokning

Skriptet väljer **övermorgon** (lokalt datum + två dagar), klickar på det första
lediga alternativet och bekräftar bokningen. Det använder Playwrights explicita
väntningar, loggar tydliga fel och återanvänder ett sparat webbläsartillstånd för
att undvika MFA vid varje körning.

> **Viktigt:** Kontrollera LiU:s och TimeEdits användarvillkor samt bokningsregler.
> Prova först under uppsikt. Webbgränssnitt kan ändras; använd då de valfria
> selektorerna i `.env.example`. En utgången MFA-session kan inte förnyas av cron.

## Installation

Python 3.10 eller senare rekommenderas.

```bash
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env                      # Windows: copy .env.example .env
```

Redigera `.env`. Filen och sessionsfilen är ignorerade av Git. Sessionsfilen
innehåller känsliga cookies; skriptet försöker ge den rättigheten `0600`. Även
användarnamn och lösenord läses från `.env` som `TIMEEDIT_USERNAME` respektive
`TIMEEDIT_PASSWORD`. Lagra aldrig dessa filer i versionshantering.

## Första inloggningen och MFA

Kör från en interaktiv grafisk session:

```bash
source .venv/bin/activate
python timeedit_booker.py --setup-session
```

Chromium öppnas synligt. Skriptet försöker fylla inloggningsuppgifterna, men du
slutför MFA själv. När bokningssidan syns går du tillbaka till terminalen och
trycker Enter. Därefter sparas cookies/local storage i `.timeedit-state.json`.
Kör sedan `python timeedit_booker.py` manuellt en gång under uppsikt.

Om ett element inte hittas: kör `PWDEBUG=1 python timeedit_booker.py`, inspektera
elementet och sätt motsvarande `TIMEEDIT_*_SELECTOR` i `.env`. Exakta selektorer
har företräde framför de inbyggda svenska/engelska reservselektorerna.

## Schemalägg kl. 22:00

### Linux (cron)

Ta reda på absoluta sökvägar med `pwd` och kör `crontab -e`. Exempel:

```cron
0 22 * * * cd /absolut/sökväg/Agama && /absolut/sökväg/Agama/.venv/bin/python timeedit_booker.py >> timeedit.log 2>&1
```

Cron använder maskinens tidszon. Kontrollera med `timedatectl`; sätt vid behov
`CRON_TZ=Europe/Stockholm` på raden före jobbet. Om sessionen löper ut visar
loggen ett fel: kör då `--setup-session` igen från en grafisk terminal.

### Windows Task Scheduler

1. Välj **Create Basic Task**, dagligen, starttid **22:00**.
2. Välj **Start a program**.
3. Program/script: `C:\absolut\sökväg\Agama\.venv\Scripts\python.exe`.
4. Arguments: `timeedit_booker.py`.
5. Start in: `C:\absolut\sökväg\Agama`.

Välj att köra endast när användaren är inloggad om Chromium-miljön kräver det.
Windows använder systemets lokala tidszon. Uppgiften gör ett försök; lägg till
återförsök i Task Scheduler om det är önskvärt, men undvik täta återförsök som
kan skapa dubbla bokningar när en bekräftelse inte kunde avläsas.

## Returkoder

* `0`: bokningen verifierades.
* `1`: inloggnings-, element- eller annat körfel.
* `2`: inget ledigt alternativ hittades.
