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

## Incrementos concluídos em `fix/ffb-core`

Foi extraído um núcleo offline da v0.13 para `ffb/effects.py` e `ffb/safety.py`. O decoder e o renderer ainda compartilham uma classe: a separação será feita junto da correção de direção/sinais, evitando uma reescrita ampla. `backends/mock.py` registra os pedidos de saída. Não há adaptador EVDEV real no pacote novo; os nomes históricos de métodos no mock apenas permitem reaproveitar e testar a política existente.

Na extração, os 18 testes ainda tiveram exatamente as 13 falhas esperadas. Em commits separados foram corrigidos:

1. **Tempo dos efeitos:** silêncio de relatórios não encerra Constant ou condições válidas. Duração `0xffff` é infinita; zero é finito; a parada ocorre na fronteira da duração. Start Delay continua valendo mesmo acima de 200 ms. A atualização de magnitude não prolonga a duração.
2. **Limites de saída:** o clamp final vem após slew. Reduzir a intensidade abaixo da saída atual atualiza o backend imediatamente, inclusive dentro do intervalo normal de taxa. Intensidade 0% e pedidos de força zero param sem esperar esse intervalo. Atualizações normais continuam limitadas pela taxa.

O pedido de replay da saída permanece em 160 ms, separado do tempo PID. O mock não simula a expiração do kernel: os testes conferem o pedido e as decisões do controlador, sem alegar uma garantia física validada. Reduções de teto e stop podem antecipar atualizações normais; a taxa de 45 Hz se aplica ao fluxo normal, não bloqueia essas ações prioritárias.

Resultado desse primeiro incremento: **32 testes, 21 passando e 11 falhas esperadas; zero testes ignorados ou falhas inesperadas**. Os 18 testes originais continuam presentes: dois casos corrigidos perderam `expectedFailure`; foram adicionados seis cenários de duração e oito de segurança. Os CSVs originais continuam com cinco arquivos e 132 registros, sem efeitos completos de corrida.

As 11 divergências abertas naquele ponto eram: direção, sinal de coeficientes, escala de saturação, envelope, Output via Set Report, Loop Count, reinício por Start, Pause/Continue, pool cheio, clipping antes de cancelamento e uploads redundantes. A etapa matemática abaixo resolve cinco delas.

Os launchers e referências **ainda usam o código histórico**, sem essas correções. A integração do núcleo novo ao UHID/EVDEV é uma etapa posterior; nenhuma configuração do Steam foi alterada. O link de compatibilidade `dfgt_bridge.py` na raiz colide com o nome do pacote em `src/`; a suite insere `src/` explicitamente no caminho de imports. Não use `python -m dfgt_bridge` na raiz como entrypoint do pacote. Um entrypoint separado será criado quando a integração estiver pronta.

## Etapa matemática concluída

Estado atual: **95 testes: 89 passando, 6 falhas esperadas, nenhum teste ignorado e nenhuma falha inesperada**. Os 18 testes originais continuam presentes. Nesta etapa foram convertidos em testes normais direção, sinais dos coeficientes, saturação unsigned, envelope e cancelamento antes do clipping. O vetor de full scale negativo usa agora o Logical Minimum real −32768; o caso −32767 continua coberto independentemente, conforme justificado em `FFB_MATH.md`.

`renderer.py` concentra funções puras de direção, condições e envelopes e um estimador de movimento separado. `mixer.py` preserva a soma antes do teto e expõe clipping/reserva opcional. `effects.py` mantém decodificação e ciclo de vida, delegando a matemática; não foi feita uma reescrita do transporte ou da entrada. O backend continua sendo mock.

Foram adicionados 63 testes sintéticos, com relógio simulado: cardinais e diagonais, flags de eixos, CP Offset, extremos signed, coeficientes e saturações assimétricos, deadband, leis dinâmicas, filtros/derivadas, envelope absoluto, ganho, atraso, sobreposição, forma de onda, seis efeitos simultâneos, clipping e reserva de transientes. Os testes numéricos incluem cancelamento de números grandes, resíduos pequenos, NaN/Inf/overflow e relógio inválido. Os testes com CSVs reais permanecem separados desses vetores e preservam os 132 registros originais.

Commits de implementação desta etapa:

| Commit | Correção |
|---|---|
| `e83b594` | Projeção de efeitos vetoriais e flags de eixos |
| `ccd5f55` | Condições signed, saturação/deadband e métricas explícitas |
| `7ac8cb3` | Envelopes absolutos e formas de onda de referência |
| `7665155` | Mistura antes de clipping e reserva opcional para transientes |
| `9eacc91` | Resposta zero sob extremos numéricos e relógio PID inválido |
| `1c8e201` | Fronteiras periódicas estáveis antes da quantização |
| `332779e` | Parada/desarme do mock quando o relógio de saída é inválido |

As seis falhas esperadas restantes são:

1. Output recebido por Set Report não usa o decoder completo.
2. Loop Count não repete efeitos finitos.
3. Start de efeito ativo não reinicia a timeline.
4. Pause/Continue não preserva o estado pausado.
5. Pool cheio sobrescreve blocos sem retornar Full.
6. Nível estável ainda provoca uploads redundantes.

Próxima etapa recomendada: corrigir transporte/ciclo de vida offline, incluindo pool, Start/Stop/Solo, loops e pausa com tempo congelado. Depois eliminar uploads redundantes sem perder a renovação do replay curto e ampliar o diagnóstico/replay. A integração física deve continuar aguardando decisão e aprovação próprias.

Ainda exigem validação física: polaridade entre ABS_X/nível EVDEV/sentido mecânico, referência de velocidade/aceleração e filtros, equivalência do atrito Coulomb ao firmware, faixa dinâmica/quantização efetiva, latência/taxa e expiração de replay. A reserva opcional muda a reprodução das condições e precisa ser avaliada conscientemente. O modo linear não promete transientes intactos quando a soma ultrapassa a capacidade física. O descritor unsigned de Ramp, Custom não implementado e ausência de captura completa de corrida permanecem limitações documentadas.
