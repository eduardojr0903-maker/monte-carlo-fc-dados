# Monte Carlo FC · dados

Coletor automático das estatísticas usadas pelo simulador Monte Carlo FC.

- `scraper/collect.py` baixa do Sofascore cada jogo encerrado da temporada atual (chutes, chutes no alvo, xG, gols, minutos e titulares) de 7 ligas: Brasileirão Séries A e B, Premier League, La Liga, Serie A, Bundesliga e Ligue 1. Se o Sofascore recusar a conexão, usa os totais da temporada do FBref para a liga.
- `.github/workflows/collect.yml` roda o coletor a cada 6 horas no GitHub Actions e salva o resultado aqui.
- `data/db/` tem um arquivo por liga, no formato que o app guarda. `data/db/_report.txt` resume a última execução.
- `data/cache/` guarda os jogos já baixados, para cada execução buscar só os novos.

Para rodar na hora: aba **Actions** → **Coletar dados** → **Run workflow**.
