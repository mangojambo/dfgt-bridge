# Desenvolvimento incremental

## Snapshot e reorganização

Antes de mover arquivos foram inspecionados os caminhos dos scripts, da suite e dos relatórios. A tag `baseline-pre-refactor` guarda o estado original de fontes, auditoria e fixtures, mais o `.gitignore`. Caches e o log foram preservados no disco, fora do commit. A busca por padrões usuais de credenciais não encontrou correspondências nos arquivos de texto revisados; não é uma garantia universal de ausência de segredos.

Dependências encontradas e tratamento:

| Referência | Tratamento |
|---|---|
| Launchers usam `$HOME/dfgt-bridge/dfgt_bridge.py` e `~/dfgt-bridge/dfgt_bridge.py` | Arquivo movido a `scripts/legacy/`, com link relativo no nome original |
| Configuração de marchas em `Path.home()/dfgt-bridge/shifter.json` | Arquivo mantido na raiz; conteúdo inalterado |
| Launcher escreve `$HOME/dfgt-bridge/launch-dirt5.log` | Log preservado em `captures/legacy/`, com link relativo na raiz |
| Suite resolve v0.8/v0.13 a partir da raiz | Suite passa a usar `reference/v0.8/` e `reference/v0.13/` |
| Suite usa glob dos cinco FFBs na raiz | Busca explícita e verificada em `archive/experiments/ffb/` mais a referência v0.13 |
| Suite lê cinco CSVs da raiz | Busca em `tests/fixtures/dirt5/`, com contagem e conteúdo preservados |
| Uso externo dos nomes v0.8/v0.13 e launchers | Links relativos preservam os comandos anteriores |
| Comentários de uso e manifesto original | Mantidos como documento histórico; mapa de movimentos resolve os nomes |
| Captura nos scripts recebe caminho por argumento | Nenhum caminho de saída reescrito |

`docs/REORGANIZATION.json` contém origem, destino e hashes originais de todos os arquivos movidos. Nenhum arquivo foi apagado. Os arquivos de referência e de experimento mantêm seus bytes; somente a suite recebe os ajustes de caminhos necessários.

A v0.13 encontrada era a versão auditada com 13 defeitos, sem uma variante corrigida adicional no workspace. Ela é preservada como referência experimental. O pacote em `src/` receberá as correções sem substituir silenciosamente a versão usada no Steam.

## Validação inicial

Antes e depois da reorganização: **18 testes, 5 passando e 13 falhas esperadas**, zero testes ignorados. O teste de entrada exige encontrar exatamente as cinco versões FFB, evitando um sucesso falso por glob vazio após os movimentos.

## Próximos incrementos

1. Extrair somente as classes necessárias ao núcleo offline, preservando o comportamento e o contrato de entrada.
2. Separar a vida dos efeitos do watchdog de saída: duração finita/infinita e atrasos legítimos não expiram por silêncio do host.
3. Aplicar o teto final depois do slew, inclusive ao reduzir intensidade, parar ou falhar.
4. Corrigir direção/sinal/escalas e envelope; depois mistura/clipping, Pause/Continue/pool/loops e uploads redundantes.
5. Consolidar diagnóstico e replay dos CSVs, sem atribuir a eles validação de corrida que não contêm.

Backend físico proprietário não faz parte deste plano imediato. Testes físicos e mudanças no SteamOS continuam dependendo de aprovação.
