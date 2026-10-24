# 🤖 Odoo PR Bot (`Botmonitorapr_codicepython`)

Bot in Python per la **Code Review automatica** e la **gestione del workflow Pull Request** di moduli **Odoo (`18.0`, `19.0`, `20.0`)**, ispirato a [`OCA/oca-github-bot`](https://github.com/OCA/oca-github-bot).

Supporta una **modalità ibrida**:
1. **Server Webhook FastAPI** (in ascolto continuo degli eventi GitHub `pull_request`, `pull_request_review`, `issue_comment`, come `oca-github-bot`).
2. **GitHub Actions & CLI** (eseguibile direttamente nelle GitHub Actions o da terminale locale senza necessità di server esterni).

---

## ✨ Funzionalità Principali

### 1. Workflow Automatico in stile OCA
- **Riconoscimento automatico della serie Odoo (`18.0`, `19.0`, `20.0`)** dal branch di destinazione della PR e aggiunta automatica della label di versione.
- **Gestione automatica delle Label PR**:
  - `wip`: applicata automaticamente se la PR è in stato Draft o il titolo inizia con `WIP:` / `[WIP]`.
  - `needs review`: applicata quando la PR è pronta e supera i controlli statici bloccanti.
  - `changes requested`: applicata se l'analisi statica rileva errori bloccanti (`ERROR`), la CI fallisce o un revisore richiede modifiche.
  - `approved`: applicata al raggiungimento della soglia di approvazioni (`REQUIRED_APPROVALS`).
  - `ready to merge`: applicata quando la PR è approvata, con CI verde e aperta da almeno `N` giorni (`READY_TO_MERGE_MIN_DAYS`).
  - `merged`: applicata automaticamente al completamento del merge tramite bot.
- **Notifica Maintainer OCA**: legge la chiave `maintainers` da `__manifest__.py` dei moduli modificati e menziona automaticamente i maintainer nel commento di review.

### 2. Code Review Statica Specifica per Odoo `18.0`, `19.0` e `20.0`

| Codice Regola | Severità | Categoria | Descrizione Controllo |
| :--- | :---: | :--- | :--- |
| `ODOO-M000` | 🔴 `ERROR` | Manifest | Segnala l'uso del file deprecato `__openerp__.py` invece di `__manifest__.py`. |
| `ODOO-M001` | 🔴 `ERROR` | Manifest | Verifica che `__manifest__.py` contenga un dizionario Python valido e valutabile staticamente. |
| `ODOO-M002` | 🔴 `ERROR` | Manifest | Verifica la presenza delle chiavi obbligatorie (`name`, `version`, `depends`, `license`, `author`). |
| `ODOO-M003` | 🔴/🟡 | Manifest | Verifica il formato versione a 5 componenti (`{serie}.X.Y.Z`) e la compatibilità con le serie supportate **`18.0`, `19.0`, `20.0`**. |
| `ODOO-M004` | 🟡 `WARN` | Manifest | Controlla che la licenza sia conforme agli standard OCA/Odoo (`LGPL-3`, `AGPL-3`, `OPL-1`, ecc.). |
| `ODOO-M005` | 🔴 `ERROR` | Manifest | Rileva chiavi manifest rimosse (`init_xml`, `update_xml`, `demo_xml`, `active`). |
| `ODOO-M006` | 🔵 `INFO` | Manifest | Consiglia l'aggiunta delle chiavi OCA `maintainers` e `summary`. |
| `ODOO-P001` | 🔴 `ERROR` | Python ORM | Rileva decoratori rimossi (`@api.multi`, `@api.one`, `@api.returns`, `@api.cr`). |
| `ODOO-P002` | 🔴 `ERROR` | Sicurezza SQL | Rileva **SQL Injection** in `cr.execute()` (uso di f-string, `%` o `.format()`) suggerendo `odoo.tools.SQL` o parametri di query. |
| `ODOO-P003` | 🔴/🟡 | Python Debug | Rileva `print()`, `breakpoint()`, `pdb.set_trace()` dimenticati nel codice. |
| `ODOO-P004` | 🟡 `WARN` | Python ORM | Segnala nuovi modelli `models.Model` / `models.TransientModel` privi di `_description`. |
| `ODOO-P005` | 🔵 `INFO` | Sicurezza ORM | Segnala chiamate `.sudo()` prive di commento di giustificazione. |
| `ODOO-P006` | 🟡 `WARN` | Python | Rileva argomenti di default mutabili (`[]`, `{}`) nelle firme dei metodi. |
| `ODOO-P007` | 🟡 `WARN` | Performance | Rileva `len(model.search(...))` suggerendo `model.search_count(...)`. |
| `ODOO-P008` | 🔴 `ERROR` | Transazioni | Rileva chiamate dirette a `cr.commit()` che rompono la transazionalità di Odoo. |
| `ODOO-P009` | 🔴/🟡 | Odoo 18/19/20 | Rileva `_sql_constraints` deprecato a favore di `models.Constraint(...)`. |
| `ODOO-P010` | 🔴 `ERROR` | Odoo 18/19/20 | Rileva `name_get()` rimosso in Odoo 18/19/20 a favore di `_compute_display_name()`. |
| `ODOO-P011` | 🔴 `ERROR` | Odoo 18/19/20 | Rileva `fields_view_get()` rimosso a favore di `_get_view()` / `get_views()`. |
| `ODOO-X001` | 🔴 `ERROR` | Viste XML | Verifica la validità sintattica di tutti i file XML. |
| `ODOO-X002` | 🔴 `ERROR` | Viste XML | Rileva il tag radice `<openerp>` obsoleto invece di `<odoo>`. |
| `ODOO-X003` | 🔴 `ERROR` | Odoo 18/19/20 | Rileva `attrs="..."` e `states="..."` rimossi a favore di espressioni dirette su `invisible`, `readonly`, `required`, `column_invisible`. |
| `ODOO-X004` | 🔴/🟡 | Odoo 18/19/20 | Rileva il tag vista `<tree>` sostituito da `<list>` in Odoo 18.0, 19.0 e 20.0. |
| `ODOO-X005` | 🟡 `WARN` | Odoo 18/19/20 | Rileva `tree` all'interno di `<field name="view_mode">` invece di `list`. |
| `ODOO-X006` | 🔵 `INFO` | Odoo 18/19/20 | Suggerisce il tag `<chatter/>` al posto del blocco legacy `<div class="oe_chatter">`. |
| `ODOO-S001` | 🔴 `ERROR` | Sicurezza ACL | Verifica intestazioni e numero colonne di `security/ir.model.access.csv`. |
| `ODOO-S002` | 🟡 `WARN` | Sicurezza ACL | Avvisa se una regola ACL concede permessi `write`/`create`/`unlink` senza specificare `group_id:id`. |
| `ODOO-S003` | 🟡 `WARN` | Sicurezza ACL | Avvisa se un modulo introduce nuovi modelli concreti (`_name`) senza file `ir.model.access.csv`. |

### 3. Code Review Assistita da AI (Gemini / OpenAI)
Se configurata la chiave API (`GEMINI_API_KEY` oppure `OPENAI_API_KEY`), il bot analizza il diff della PR per individuare problemi semantici su Odoo 18.0, 19.0 e 20.0 (query N+1 dentro cicli `for`, `@api.depends` mancanti, sicurezza record rules).

### 4. Comandi Interattivi nei Commenti PR (`/bot` e `/ocabot`)
Scrivendo un commento nella PR puoi controllare il bot:
- `/bot review` (oppure `/ocabot review`): riesegue la review completa della PR.
- `/bot bump patch` | `/bot bump minor` | `/bot bump major`: aggiorna automaticamente la versione a 5 componenti in `__manifest__.py` e fa commit sul branch della PR.
- `/bot merge [patch|minor|major|nobump]`: verifica che non ci siano errori statici e che la CI non sia fallita, incrementa opzionalmente la versione in `__manifest__.py`, effettua il merge della PR e applica la label `merged`.
- `/bot help`: mostra la tabella dei comandi disponibili.

---

## 🚀 Installazione ed Utilizzo

### 1. Setup Ambiente Locale
```bash
python -m venv .venv
# Su Windows:
.venv\Scripts\pip install -r requirements.txt
# Su Linux/macOS:
.venv/bin/pip install -r requirements.txt
```

### 2. Scansione Locale di una cartella di moduli Odoo (18.0, 19.0, 20.0)
```bash
python -m odoo_pr_bot.cli scan ./percorso/addons --series 19.0
```

### 3. Review di una PR remota su GitHub da CLI
```bash
export GITHUB_TOKEN="ghp_..."
python -m odoo_pr_bot.cli review-pr --repo owner/repo --pr 42
```

### 4. Avvio come Server Webhook FastAPI
```bash
cp .env.example .env
# Configura GITHUB_TOKEN e GITHUB_WEBHOOK_SECRET in .env
python -m odoo_pr_bot.cli serve --host 0.0.0.0 --port 8000
```
Oppure tramite **Docker Compose**:
```bash
docker compose up -d --build
```
Configura poi su GitHub (**Settings -> Webhooks**) l'URL `https://tuo-dominio/webhook` con `Content type: application/json` selezionando gli eventi:
- `Pull requests`
- `Pull request reviews`
- `Issue comments`

### 5. Utilizzo con `pre-commit` (Controllo automatico prima di ogni `git commit`)
Il repository espone un hook ufficiale definito in [`.pre-commit-hooks.yaml`](.pre-commit-hooks.yaml) e un template completo pronto all'uso in [`templates/.pre-commit-config.odoo.yaml`](templates/.pre-commit-config.odoo.yaml).

Aggiungi questo blocco nel file `.pre-commit-config.yaml` dei repository Odoo (`18.0`, `19.0`, `20.0`) della tua organizzazione:

```yaml
repos:
  - repo: https://github.com/enigmista91/Botmonitorapr_codicepython
    rev: main
    hooks:
      - id: odoo-pr-bot
        args: ["--series", "18.0"] # Oppure "19.0" o "20.0"
```

Poi attiva l'hook localmente con:
```bash
pip install pre-commit
pre-commit install
```
Ad ogni `git commit`, il bot analizzerà istantaneamente i file modificati (`.py`, `.xml`, `.csv`, `.yaml`) bloccando il commit in caso di errori critici.

### 6. Utilizzo tramite GitHub Actions (Zero-Server)
Il file [`.github/workflows/odoo-pr-bot.yml`](.github/workflows/odoo-pr-bot.yml) è già pronto per eseguire automaticamente il bot ad ogni apertura/aggiornamento di PR (sui branch `18.0`, `19.0`, `20.0`, `main`, `master`) e ad ogni commento `/bot` o `/ocabot`.

---

## 🧪 Esecuzione dei Test
```bash
pytest -v
```
