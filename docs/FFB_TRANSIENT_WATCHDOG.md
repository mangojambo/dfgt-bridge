# Watchdog após Stop PID — correção candidata

O log real informado pelo operador identifica `watchdog_calculation_stale`,
idade 105196896 ns, jogo ativo, `playing=false`, nível zero, último PID
`5a010300` (Effect Operation: Stop, bloco 1), stop anterior de PID,
`hard_fault=false` e nenhuma emergência. A causa do latch está comprovada:
o watchdog de 100 ms era aplicado como latch também a uma saída já parada.
O log não determina se o intervalo veio de agendamento, IPC ou descartes.

Na candidata, a proteção mantém três comportamentos distintos:

| Situação | Comportamento |
|---|---|
| Sem cálculo aceitável por 100 ms, força ainda ativa | Zero imediato e latch; exige arm humano. |
| Sem cálculo aceitável por 100 ms, saída já parada | Mantém zero e aguarda cálculo posterior ao timeout; não trava a sessão. |
| F9, teclado perdido, emergência, saída do jogo, cálculo inválido ou falha de I/O | Latch preservado; hard faults continuam terminais. |

Para retomar após espera transitória são necessários jogo ativo, ausência de
latch, geração estritamente nova, época atual, timestamp de cálculo posterior
ou igual ao instante da espera e idade <100 ms. Cálculos anteriores à espera
e pacotes de épocas invalidadas não renovam a força. Gerações repetidas,
timestamp futuro, números não finitos e relógio sem avanço não são
convertidos em recuperação automática. O kernel continua recebendo efeitos
de no máximo 160 ms, com o guard independente antes/depois de operações EVDEV.

Uma parada tem prioridade sobre a verificação de watchdog daquela ação:
primeiro zera, depois decide se deve aguardar. F9 e emergências mantêm seu
próprio motivo original mesmo quando chegam na fronteira de 100 ms.

O produtor agora consulta as transições do decoder: repetir um Stop já
aplicado, sem mudança de estado, não avança novamente `stop_epoch`. A primeira
parada, mudanças de efeitos/atuadores, UHID Close e passagem efetiva para zero
continuam criando barreiras. Produtores antigos sem metadados de transição
mantêm o comportamento conservador. Não foi comprovada uma tempestade de
Stop na sessão real; a regressão demonstra e elimina esse caminho evitável.

`[FFB WAIT]` registra uma espera por interrupção; `[FFB RESUME]` registra a
geração fresca que a encerrou. Polls repetidos não geram fluxo contínuo de
eventos. F10 inclui `transient_recovery`, com contadores e último timeout.
O diagnóstico de latch original permanece disponível quando houver latch.
Esta etapa introduz trace v4; a [calibração posterior](DFGT_CALIBRATION.md)
usa v5. Replays antigos que dependam de latch durante
repouso devem divergir explicitamente, pois essa decisão foi corrigida.

Regressões em `tests/test_ffb_latch_diagnostics.py` reproduzem a idade real,
1.947 frames ultrapassados por Stop, três corridas com lacunas de 500 ms,
Stop/Start repetidos, pausas de mais de dois segundos, geração nova com
timestamp velho, épocas antigas e F9 durante espera. Os testes anteriores
de watchdog com força ativa continuam exigindo latch. Dois testes que antes
exigiam latch com saída parada foram atualizados para o comportamento pedido;
seus asserts de zero, rejeições, épocas e fronteira de 100 ms foram mantidos.

Validação desta etapa: 519 testes, 518 passaram e um smoke Tk ignorado por
falta de display. Sem hardware, instalação, alteração de drivers ou push.
A validação de várias corridas reais no SteamOS permanece pendente.
