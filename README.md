# Monte Carlo FC · dados

Coletor automático das estatísticas usadas pelo simulador Monte Carlo FC.

- `scraper/collect.py` baixa do FotMob (dados Opta) cada jogo encerrado da temporada atual — chutes, chutes no alvo, xG, gols, minutos e titulares — de 8 ligas: Brasileirão Séries A e B, Premier League, Championship, La Liga, Serie A, Bundesliga e Ligue 1. Para os jogos dos próximos 8 dias, traz a escalação (confirmada, provável ou último XI), os desfalques por lesão ou suspensão e a escalação provável do Sportsgambler. `scraper/collect_espn.py` é a versão anterior (ESPN + Understat), mantida como reserva.
- `.github/workflows/collect.yml` roda o coletor a cada 3 horas no GitHub Actions e salva o resultado aqui.
- `data/db/` tem um arquivo por liga, no formato que o app guarda. `data/db/_report.txt` resume a última execução.
- `data/cache/` guarda os jogos já baixados, para cada execução buscar só os novos.

Para rodar na hora: aba **Actions** → **Coletar dados** → **Run workflow**.
