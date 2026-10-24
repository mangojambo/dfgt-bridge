# Capturas locais

Capturas novas e logs ficam aqui e são ignorados pelo Git. Os cinco CSVs pequenos da auditoria foram movidos sem alteração para `tests/fixtures/dirt5/`.

O log preexistente foi preservado em `legacy/launch-dirt5.log`, fora do versionamento. Duas capturas reais passivas fornecidas pelo operador foram analisadas sem alteração dos arquivos; comparação e hashes estão em [CAPTURE_COMPARISON_20261024.md](../docs/CAPTURE_COMPARISON_20261024.md). Os CSVs legados continuam sendo corpus de inicialização; nenhuma coleta ou reprodução física é executada pelos testes.

O runner novo aceita JSONL v1 ou JSONL gzip, preservando os eventos brutos completos. Para uma corrida inteira, prefira `--capture captures/NOME.jsonl.gz` e marque `menu`/`race` pelo console. Comandos e limites estão em [PASSIVE_RUNNER.md](../docs/PASSIVE_RUNNER.md). Replay: `python3 -B scripts/replay-capture.py captures/NOME.jsonl.gz`, na raiz do projeto. As fixtures sintéticas de teste ficam fora deste diretório e não representam uma coleta real.
