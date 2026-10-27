# FFB bloqueado: diagnóstico e regressões offline

Atualização após novo log real: o latch com saída já parada foi identificado
e recebeu [correção de transições](FFB_TRANSIENT_WATCHDOG.md). O diagnóstico
abaixo registra a etapa anterior; calibração e validação atuais estão em
[DFGT_CALIBRATION.md](DFGT_CALIBRATION.md).

Data: 2026-10-26. Alterações candidatas em `fix/ffb-core`; a versão pública
v1.0.1 e `release-checkout/` permanecem preservadas. Sem teste físico,
instalação, mudança no SteamOS ou push.

## Evidência e limite da conclusão

Os dois F10 informados pelo operador são observações reais da mesma sessão.
Às 17:35 havia força física comandada; às 17:40 havia latch, ganho comandado
zero, nenhuma reprodução e força calculada diferente de zero. Isso confirma
bloqueio pelo supervisor. Não determina o gatilho original: o código anterior
sobrescreve `zero_reason` ao descartar um frame anterior ao Stop, ao aguardar
cálculo pós-arm e ao ajustar intensidade para zero. Uma nova parada com latch
também troca o motivo e o timestamp. `hard_fault=false` não exclui watchdog,
F9, perda do teclado, emergência ou saída transitória do processo.

Não há stream temporal desses F10 no repositório. Não é possível atribuir
retrospectivamente os 1.947 descartes a um gatilho único.

## Caminhos de latch auditados

| Gatilho | Condição e consequência |
|---|---|
| `watchdog_calculation_stale` | `now-last_valid_ns >= 100 ms`, verificado antes de toda ação, inclusive antes de receber um novo frame. `poll` nunca renova força. |
| `invalid_or_stale_calculation` | Geração repetida/regressiva, cálculo futuro ou com idade ≥100 ms, componentes incompletos ou não finitos. |
| `calculation_clock_not_advancing` | Nova geração, mas timestamp de cálculo ≤último timestamp válido. |
| `game_exit` | Processo já observado deixa de ser reconhecido. Identidade é PID + starttime; rediscovery não rearma. Erro transitório de leitura de `/proc` também retorna ausência. |
| `F9_or_keyboard_loss` | F9 e falhas de teclado eram indistinguíveis; EOF, erro, evento parcial e SYN_DROPPED geram parada. Falta inicial de teclado apenas informa o fallback de terminal. |
| `terminal_SIGUSR1`, `terminal_emergency` | Sinal ou barreira de emergência compartilhada; não dependem da entrega de frames. |
| `supervised_session_deadline` | Fatal, somente em perfil com duração finita explícita; não se aplica ao default unlimited. |
| `output_exception` | Exceção de saída fecha/limpa o backend, marca hard fault; inclui falha do guard imediatamente antes/depois de I/O. |

Stop/Reset/Disable/Pause/Free/UHID Close e passagem da soma para zero avançam
`stop_epoch` no produtor. No worker, isso para imediatamente e eleva
`minimum_epoch`. Não são latch por si mesmos. Frames abaixo dessa barreira
não podem atualizar `last_valid_ns`, `last_generation` ou restaurar força.

## Reproduções antes da correção

Vetores **sintéticos**, com MockBackend e nanossegundos determinísticos:

1. Frame g1 em 1 ms, Stop epoch1 em 2 ms, frame g3/epoch1 em 90 ms;
   depois chega g2/epoch0 calculado em 3 ms, recebido em 110 ms.
   Resultado anterior: `invalid_or_stale_calculation`, latch, embora o cálculo
   válido tenha apenas 20 ms. A validação de idade/geração precedia a barreira
   que já tornava esse pacote inelegível. Correção proposta: descartar primeiro
   o pacote de época invalidada, mantendo o watchdog de 100 ms intacto.
   Este é um teste da fronteira da política com entrega reordenada. O IPC real
   usa SOCK_SEQPACKET FIFO e um único frame em trânsito; não se demonstrou
   que essa ordem de chegada possa acontecer nesse transporte. A mudança é
   defensiva e não é apresentada como causa confirmada do incidente.
2. Após um cálculo válido, a cada 5 ms uma nova barreira ultrapassa o frame
   em trânsito. Após 100 ms sem cálculo **aceitável**, o watchdog trava.
   Após 1.947 frames: `latched=true`, `safety_events=1`, `rejected=1947`,
   `zero_reason=discarded_pre_stop_frame`. Reproduz o padrão diagnóstico,
   mas não demonstra que essa tempestade aconteceu no DiRT 5 real.

O latch sozinho não incrementa `rejected`: frames novos, frescos e da época
atual continuam aceitos para diagnóstico, com saída bloqueada. Logo, os 1.947
não podem ser explicados apenas por `latched=true`. Podem incluir descartes
de barreira, cálculos inválidos ou anteriores ao arm; faltava discriminação.

Menus e loading não suspendem os ticks do runner (~22 ms mais agendamento);
silêncio de relatórios PID não expira efeitos infinitos. Uma pausa real do
renderer/IPC/worker ≥100 ms ainda deve travar. Não se aumenta o prazo para
encobrir essa condição, nem se rearma ao detectar novamente o processo.

## Escopo da correção candidata

Preservar a primeira entrada em latch até o fim da sessão, distinguir o
motivo atual de descarte e contar rejeições por motivo. Registrar um evento
compacto na entrada do latch, sem captura contínua: monotonic time, categoria,
ação, identidade do jogo, geração/idade, épocas, último PID recebido,
stop anterior e estado da saída/worker, com I/O EVDEV antes/depois.

Somente pacotes de época já invalidada mudam de classificação: são descartados
antes da validação de cálculo. Não renovam watchdog nem escrevem força.
Rearme continua explícito, com jogo ativo, sem hard fault, pedido posterior ao
latch e cálculo posterior ao arm, geração nova e época atual. O primeiro
diagnóstico permanece mesmo depois desse rearme.

## Fidelidade dos efeitos

O núcleo atual em `src/` já difere da v0.13 auditada. Não se deve reatribuir
ao núcleo todos os defeitos históricos. Revisão do decoder/renderer:

| Família | Escala e sinal implementados | Limite da evidência |
|---|---|---|
| Constant | s16, envelope absoluto, projeção X por direção, ganho de efeito /100 e dispositivo /255 | `-1` pode ser nível pequeno, ganho, projeção ou soma; sem parâmetros não prova erro de escala. |
| Spring | Posição normalizada, CP Offset e coeficientes signed; saturação/deadband u16 /65535; reação restauradora para coeficiente positivo | `-2521.08` é compatível com deslocamento e coeficientes; faltam posição e parâmetros para reconstruir esse valor real. |
| Damper | Mesma lei condicional com velocidade /2 unidades de posição/s | Referência e filtro de 45 ms são escolhas do sintetizador, sem calibração G920. |
| Friction | Coulomb fora do deadband de velocidade, coeficientes signed | Não mede torque estático; não equivale ao firmware. |
| Inertia | Condição por aceleração /4 unidades de posição/s² | Aproximação dependente da derivada/filtro e da amostragem. |
| Periodic | Magnitude u16 limitada a 32767, offset s16, fase em centésimos de grau; onda/envelope/direção antes da soma | Taxa de saída de até 45 Hz limita frequências reproduzíveis; aliasing continua possível. |

As famílias são somadas antes de clipping; polaridade −1 e ganho 100% entram
na política de saída, sem novo teto por família. O snapshot funcional tem
`-2521.08 + -1 = -2522.08`; EVDEV positivo é coerente com polaridade −1.
Diferença de módulo para 2535 pode refletir gate/slew entre frames; os dois
números não bastam para provar paridade temporal. O snapshot bloqueado soma
aproximadamente 446.30 e seria elegível a força diferente de zero sem o latch.
Famílias em zero num instante não provam ausência ao longo de corridas.

Fontes primárias consultadas: [USB PID 1.0, condições e efeitos](https://www.usb.org/sites/default/files/documents/pid1_01.pdf)
e [Linux hid-pidff, conversões signed/unsigned e direção](https://github.com/torvalds/linux/blob/master/drivers/hid/usbhid/hid-pidff.c).
São referências semânticas, não captura de um G920 físico nem prova da versão
Proton/kernel desta sessão. Convenções e aproximações locais:
[`FFB_MATH.md`](FFB_MATH.md). Nenhuma escala de efeito é alterada nesta correção.

## Validação

Baseline: 494 testes, 493 passaram e um smoke Tk foi ignorado sem display.
Comando offline: `python3 -B -m unittest discover -s tests -v`.
As regressões específicas e o resultado após a correção são registrados abaixo
quando concluídos. A validação das transições reais no SteamOS continua pendente;
esta entrega não declara o incidente físico resolvido.

Resultado final: **511 testes, 510 passaram e um smoke Tk ignorado por falta
de display**, zero falhas esperadas. A mesma suite passou com audit hook que
bloqueia abertura de dispositivos, bind/unbind/new_id e subprocessos reais,
com ResourceWarning tratado como erro: **zero tentativas**. Dois testes antigos
do launcher precisaram simular `notify-send`, evitando notificações do desktop.
Hashes de v0.8/v0.13 conferidos com a proveniência existente; fixtures intactas.

Reprodução focada, sem hardware:

```sh
python3 -B -m unittest discover -s tests -p test_ffb_latch_diagnostics.py -v
python3 -B -m unittest discover -s tests -p test_physical_worker.py -v
```

São 11 vetores novos no primeiro arquivo: pacote antigo invalidado, tempestade
de 1.947 descartes, fronteira exata de 100 ms, latch sem rejeições, persistência
do primeiro gatilho, rediscovery, arm explícito e geração/época novas, mailbox
com pool cheio e duas transições, e sequências PID de duas corridas com menu,
loading, Pause/Continue, Disable/Enable, Free, Stop All e Reset. O segundo
arquivo verifica também a linha de log sem captura, I/O antes/depois, falhas
de saída/setup, retenção do último PID através de coalescing e cleanup.

## Leitura do novo diagnóstico

- `[FFB LATCH]` é emitido uma vez por transição para latched, mesmo sem
  `--capture`/trace; o journal já recebe stdout do serviço. `timestamp_ns` é o
  instante monotônico da ação da política; `worker.observed_ns` marca a coleta
  do estado I/O após a parada. Não são timestamps de torque medido.
- `safety_diagnostic.first_latch` nunca é trocado até encerrar a sessão,
  inclusive após arm explícito. `last_latch` descreve a última transição;
  repetição de Stop enquanto já travado não cria outra transição.
- `zero_reason` preserva o gatilho enquanto travado. `last_rejection` é
  separado; `rejected_by_reason` permite distinguir barreira, cálculo inválido,
  relógio parado e cálculo anterior ao arm. `safety_events` conta transições.
- `last_pid_command` é o último relatório PID **recebido pelo produtor**,
  com timestamp, geração e erro de transporte, retido mesmo se sobrescrito
  por input/tick antes do envio. Não prova causalidade nem aplicação do pacote.
- `first_latch_io` no F10/status preserva PID/estado do worker, épocas
  compartilhadas, erros recentes de teclado e telemetria EVDEV antes/depois.
  Os snapshots de latch não duplicam os 40 efeitos ou componentes completos.
- O mailbox de **status** passa a 16 KiB fixos; comandos/frames continuam em
  4 KiB e sem fila. Não há arquivo novo por padrão nem histórico ilimitado.
- Trace opcional usa diagnóstico v3. Replay exige a evidência v3 e detecta
  omissão/adulteração. Traces v1/v2 podem omitir apenas os campos aditivos que
  não existiam; todos os campos e decisões originais continuam comparados.
  Se a mudança de decisão ou persistência de motivo divergir de um trace
  antigo, o replay deve rejeitá-lo, em vez de declarar compatibilidade falsa.

Uma futura validação autorizada no SteamOS deve atravessar ao menos duas
corridas, menus e loading, preservando os defaults atuais. Se houver latch,
a linha original e um F10 posterior bastam para comparar idade/generation,
épocas e rejeições, ou verificar desaparecimento/identidade do processo e
teclado. Nenhuma recuperação automática foi adicionada e nenhuma instalação
ou sessão física foi executada nesta entrega.
