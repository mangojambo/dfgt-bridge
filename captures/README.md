# Capturas locais

Capturas novas e logs ficam aqui e são ignorados pelo Git. Os cinco CSVs pequenos da auditoria foram movidos sem alteração para `tests/fixtures/dirt5/`.

O log preexistente foi preservado em `legacy/launch-dirt5.log`, fora do versionamento. Não há captura completa dos efeitos de corrida no corpus atual. Consulte `docs/FFB_AUDIT.md` antes de coletar novos dados; nenhuma coleta ou reprodução física é executada pelos testes.

O runner novo aceita JSONL v1 ou JSONL gzip, preservando os eventos brutos completos. Para uma corrida inteira, prefira `--capture captures/NOME.jsonl.gz` e marque `menu`/`race` pelo console. Comandos e limites estão em [PASSIVE_RUNNER.md](../docs/PASSIVE_RUNNER.md). Replay: `python3 -B scripts/replay-capture.py captures/NOME.jsonl.gz`, na raiz do projeto. As fixtures sintéticas de teste ficam fora deste diretório e não representam uma coleta real.
