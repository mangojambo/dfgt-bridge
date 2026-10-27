# FFB EVDEV supervisionado — controles, proteção e diagnóstico

**Defaults oficiais atualizados:** 100%/100%, polaridade −1, slew 327670 e duração ilimitada no perfil normal. CLI e GUI compartilham perfis; entrada Y/G29/PIDFF. Esta decisão substitui as recomendações iniciais de ensaio conservador, que são históricas. Segurança e modo físico explícito permanecem. Ver [PROFILES_AND_MAPPING.md](PROFILES_AND_MAPPING.md).

O acelerador em Y foi confirmado pelo operador. Esta etapa adiciona uma opção física ao runner, conservando o caminho passivo como padrão e sem modificar descritor, entrada, referências ou launchers. Durante o desenvolvimento foram usados somente mocks, relógios simulados e replay; nenhum motor foi acionado, driver instalado ou configuração do SteamOS alterada.

A inicialização exige associação PIDFF, entrada EVDEV virtual e FF_CONSTANT antes de mostrar `[PRONTO]` ou criar o processo de saída física. A associação automática e os limites da regra dinâmica estão em [PID_BINDING.md](PID_BINDING.md). O operador já testou 5% e 10% e relatou pouca força. Agora intensidade e máximo aceitam 0..100%, sem boost; veja [FFB_INTENSITY.md](FFB_INTENSITY.md) para a auditoria, o efeito do timeout de 30 s nos traces reais e o comando de 100% com slew explícito.

## Comandos preparados

Modo passivo, com o mesmo comportamento anterior:

```sh
cd /path/to/dfgt-bridge
sudo /usr/bin/python3 -B scripts/run-passive.py --gas-axis y
```

Modo físico supervisionado, para quando o operador decidir realizar o ensaio:

```sh
sudo /usr/bin/python3 -B scripts/run-passive.py \
  --enable-ffb --gas-axis y \
  --ffb-strength 100 --ffb-max-strength 100 --ffb-polarity -1 \
  --ffb-slew-rate 327670 --ffb-seconds unlimited \
  --capture captures/dirt5-ffb-01.jsonl.gz \
  --physical-trace captures/dirt5-ffb-policy-01.jsonl.gz \
  --proton 'INFORMAR VERSÃO EXATA' \
  --scenario 'DiRT 5 - primeiro ensaio EVDEV supervisionado'
```

As capturas são exclusivas: escolher outros nomes se já existirem. Quando há `--capture` e não há `--physical-trace`, o nome automático do trace é o nome completo da captura acrescido de `.ffb.jsonl.gz`. O modo passivo não abre esse trace nem cria processo de saída, mesmo se forem informadas opções físicas sem `--enable-ffb`.

Python 3.10+ e o suporte EVDEV/UHID já existente são suficientes; não foi adicionada dependência de `python-evdev`. O backend físico suporta a ABI Linux **x86_64**, `input_event=24`, `ff_effect=48`. Outros alvos falham antes de abrir o dispositivo físico de saída.

## Arquitetura e isolamento

O processo principal continua usando `PassiveSession`: entrada v0.8 → transporte PID → pool/timeline → renderer → política mock de referência. Nenhum backend físico entra em `OfflineSession`. A captura principal conserva seu formato e seus resultados determinísticos.

Somente com `--enable-ffb`, `PhysicalClient` cria por `spawn` outro processo, que possui o fd de saída EVDEV e lê os teclados. Recebe os componentes **antes do ganho/teto do mock**, calculados pelo mesmo renderer; aplica a política `ConstantOutput` do núcleo com o perfil físico, e envia um único Constant ao DFGT. O ganho mock padrão de 20% não é multiplicado pelo ganho físico; o perfil normal físico usa 100%.

IPC usa SOCK_SEQPACKET não bloqueante e crédito/ACK: no máximo **um frame em trânsito e um último valor local**, substituído por cálculos recentes. Não há fila de forças antigas. Gerações e timestamps são monotônicos; o consumidor não aceita a mesma geração ou o mesmo instante de cálculo como renovação válida. Frames/comandos são limitados a 4096 bytes; status, incluindo evidência de latch, a 16384 bytes. Mailboxes usam seqlock sem espera no alvo x86_64; um escritor parado no meio da atualização não prende o leitor.

Stop/Reset/Disable/Pause/Free/CLOSE e transição para soma zero também avançam uma barreira independente do pacote pendente. Frames anteriores à barreira não podem restaurar força. Parada de um efeito interrompe o agregado e permite recompor os outros com um cálculo novo; não altera as fórmulas de mistura. A emergência de terminal tem uma barreira separada, que não se perde se outro comando substituir a mailbox.

## Backend físico

`backends/evdev.py` exige opt-in antes de qualquer open de saída. Abre o mesmo caminho do DFGT usado para entrada, mas com fd próprio O_RDWR, valida novamente **046d:c29a**, consulta FF_CONSTANT/FF_GAIN/FF_AUTOCENTER e quantidade de slots. G920, G29 virtual/físico e qualquer identidade diferente são recusados antes de comandos de força. A leitura de entrada mantém seu fd O_RDONLY original.

Só utiliza EVIOCSFF de tipo FF_CONSTANT, play/stop do próprio ID, FF_GAIN, FF_AUTOCENTER e EVIOCRMFF. Um único ID é reutilizado; mudanças de nível atualizam esse ID e níveis idênticos não causam upload repetido. Play/renovação são separados de upload. Não há chamadas USB/hidraw nem bytes Logitech proprietários enviados pelo bridge. Naturalmente, o driver Linux traduz os pedidos EVDEV para seu protocolo de hardware.

Inicialização validada coloca ganho em zero e autocenter em zero. Um play autorizado coloca FF_GAIN em 65535; o nível Constant já está limitado pelo software. Stop corta ganho e para o slot. Cleanup tenta ganho zero, stop e erase, fechando o fd mesmo após desconexão/erro. Não restaura ganho/autocenter anteriores, pois a interface não oferece uma leitura confiável desses valores: **permanecem em zero ao encerrar**. O backend físico não altera drivers, rotação, descritor, udev ou configuração SteamOS; a preparação transitória do driver virtual é separada e documentada em [PID_BINDING.md](PID_BINDING.md).

Há flock cooperativo no fd. Isso evita outro bridge que respeite a mesma trava, mas **não impede Steam, Oversteer, hidraw ou outros escritores independentes**. FF_GAIN e FF_AUTOCENTER são propriedades globais do DFGT. O primeiro ensaio exige que não haja outro controlador de FFB físico ativo e que o jogo use o G29 virtual; o programa não modifica automaticamente essa seleção.

Referências de implementação: [UAPI input.h](https://github.com/torvalds/linux/blob/master/include/uapi/linux/input.h), [EVDEV FF](https://docs.kernel.org/input/ff.html) e [hid-lg4ff](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-lg4ff.c). O antigo teste físico e a v0.13 foram consultados, sem executá-los ou modificá-los.

## Limites, watchdog e saída do jogo

| Proteção | Política |
|---|---|
| Intensidade inicial | **100% padrão oficial**; aceita 0..100%, sem exceder o máximo configurado |
| Teto ajustável | **100% padrão oficial**; configurável 0..100%; F7/F8 ajustam 5 pontos dentro de 0..máximo |
| Slew físico | `--ffb-slew-rate`: padrão **327670 unidades/s**; escala completa em aproximadamente 0,1 s; dt limitado a 50 ms |
| Prioridade | Zero, redução de teto e parada não aguardam slew/rate gate |
| Atualização normal | No máximo 45 Hz; paradas e reduções de segurança podem antecipar |
| Idade de cálculo | 100 ms sem avanço com força ativa → stop e latch; com saída já parada → aguardar frame posterior ao timeout. PID não é heartbeat. |
| Duração EVDEV | Cada replay tem no máximo 160 ms; renovar exige cálculo novo e recente |
| Supervisor | Poll nominal de 5 ms; processo principal detecta status parado por 250 ms e encerra o filho |
| Sessão física | **Ilimitada no perfil normal**; prazo 30..240 s somente por opção explícita; watchdog independente permanece |
| Jogo | Aguardar processo DiRT5.exe; saída ou troca de identidade PID/starttime → stop e latch |

O supervisor testa idade antes de operações EVDEV, inclusive entre upload e play. Um upload que retorna tarde não autoriza play antigo. O poll do watchdog **nunca** renova força: só um frame novo do renderer pode chegar a `ConstantOutput.tick()`. Efeitos HID infinitos continuam válidos enquanto o renderer avança, mesmo sem novos relatórios do jogo.

Na candidata de desenvolvimento, [a espera transitória](FFB_TRANSIENT_WATCHDOG.md)
exige geração nova, época atual, idade <100 ms e cálculo posterior ao timeout.
Stop repetido sem mudança de estado não invalida continuamente frames novos.
F9, perda do teclado, saída do jogo e falhas graves preservam latch.

Ganhos independentes por família entram depois do renderer PID e antes da
mistura final. Na versão v1.1.0, `ffb_spring_gain=3.0` é padrão;
os demais ganhos são ×1. A reinstalação migra perfis antigos automaticamente
e preserva valores personalizados explícitos. Não são necessários argumentos
extras na Steam. Ver [calibração DFGT e migração](DFGT_CALIBRATION.md).

O filho continua capaz de desligar quando o renderer principal trava ou recebe SIGSTOP. Se o próprio filho travar, a proteção restante é o replay finito no kernel; o pai também encerra um filho sem avanço. O agendamento de replay finito está em [ff-memless](https://github.com/torvalds/linux/blob/master/drivers/input/ff-memless.c). **O prazo real no kernel/driver/firmware Valve ainda precisa ser medido no ensaio físico**; estes são limites de software, não uma certificação de tempo real ou proteção contra falhas de USB/kernel/alimentação.

O monitor lê `/proc` sem alterá-lo. Descobre `DiRT5.exe`/`dirt5` por `comm`, sem diferença de caixa; compara PID e starttime e rejeita zombies. `--game-pid PID` permite escolher uma instância específica, mas exige a mesma identificação. Enquanto não encontrar o jogo, o FFB fica zero; uma nomenclatura diferente precisa ser investigada, não presumida. O monitor é necessário porque Steam/Wine podem manter leitores UHID abertos após a saída do jogo. Nova instância do jogo não rearma um latch anterior.

Exceção de dispositivo ou morte do supervisor encerra o runner com cleanup. Sinais tratados e EOF removem o slot e os recursos. SIGKILL não executa finally Python: o fd fechado pelo sistema e o replay finito são as proteções disponíveis. Manter um meio físico de cortar alimentação acessível durante a primeira validação.

## Controles globais e terminal

| Controle | Efeito |
|---|---|
| F5 | Spring ×3 temporário, interpolação de 300 ms |
| F6 | Spring ×4 temporário, interpolação de 300 ms |
| F7 | Reduz teto físico em 5 pontos; mínimo zero |
| F8 | Aumenta em 5 pontos até o teto configurado; não rearma emergência |
| F9 | Stop imediato e latch; repetir F9 continua desligado |
| F10 | Efeitos, componentes, ganho/teto, nível EVDEV, clipping, uploads, idade e latência |
| `ffb-stop` + Enter | Emergência de terminal; latch independente da mailbox de outros comandos |
| `ffb-arm` + Enter | Reativação explícita, somente com jogo ativo, sem falha fatal e com cálculo posterior ao comando |
| `ffb-strength N` + Enter | Teto físico absoluto, 0..máximo configurado |
| `ffb-status` + Enter | Snapshot do supervisor |
| Ctrl+C / `quit` | Encerra tudo e remove o efeito |

F5/F6 não salvam perfis, não reiniciam UHID e não rearmam latch. Repetição
automática é ignorada. F9 é processado antes das demais teclas do mesmo lote.
F10 distingue ganho selecionado/aplicado/associado ao comando, Spring antes e
depois da calibração, pico e saturação da sessão e dos últimos cinco segundos.
Definições, sincronização, conflitos e limites de Gaming Mode:
[Spring em tempo real](LIVE_SPRING.md).

Os teclados são abertos O_RDONLY e sem grab no processo físico. Assim as teclas não dependem do foco do DiRT 5. Por padrão são procurados aliases `*-event-kbd` em by-id/by-path, exigindo teclas de teclado e F7..F10 e eliminando aliases duplicados. `--keyboard-event /dev/input/eventN` seleciona um teclado explicitamente. Repetições e releases não alteram intensidade. SYN_DROPPED ou perda de um teclado causam emergência; após reconectar, reiniciar o runner para redescobri-lo.

Se não houver teclado acessível, a mensagem diz **INDISPONÍVEIS** e informa o PID do supervisor. Continua disponível a parada independente em outro terminal:

```sh
kill -USR1 PID_DO_SUPERVISOR_FFB
```

Esse sinal é tratado pelo filho, mesmo se o renderer principal estiver parado. F9, sinal e `ffb-stop` não alternam para ligado. O comando antigo `strength` continua ajustando somente o mock de referência; para hardware usar F7/F8 ou `ffb-strength`.

## Diagnóstico e replay pareado

A captura principal mantém os bytes PID e o estado completo do núcleo. O trace físico separado usa os mesmos registros encadeados/versionados e declara `metadata.kind=physical_policy` e o perfil utilizado. Registra frames realmente consumidos, instante de cálculo e consumo, ações Stop/Arm/Strength/Game/Poll/Close e resultados da política. Não há hardware no replay.

Campos físicos distinguem:

- `game_force`: níveis Constant recebidos e ganho PID; parâmetros completos dos demais efeitos permanecem nos relatórios brutos/estados da captura principal.
- `effects` e `renderer_components`/`renderer_total`: blocos, tipos e forças calculadas antes do teto físico.
- `requested_before_limit`/`limited_force`: pedido após ganho físico e resultado do teto, antes de slew.
- `evdev_level`: nível quantizado pedido ao EVDEV depois de slew e clamp final; não é medição de torque mecânico.
- `uploads`, `deduplicated`, `rate_limited`, `plays`, `renewals`: uploads concluídos pelo controlador, uploads evitados, frames bloqueados pela taxa e renovações.
- `latency_ns`, `max_latency_ns`, `age_ns`, `io.last_io_ns`/`io.max_io_ns`: idade desde o cálculo e duração medida das chamadas EVDEV. Tempos de CPU/I/O são metadados, não relógio oculto do replay.
- `zero_reason`, `latched`, `hard_fault`, `safety_events`: causa do zero e desligamentos de segurança. Não se apagam rejeições PID/proprietárias da captura principal.

Comparar um par capturado:

```sh
python3 -B scripts/replay-capture.py captures/dirt5-ffb-01.jsonl.gz \
  --physical-trace captures/dirt5-ffb-policy-01.jsonl.gz
```

Com outra revisão dos fontes, adicionar `--verify-compatible`; divergências continuam sendo erro. O replay primeiro confere todos os resultados PID/renderer da captura principal. Depois recria a política supervisionada com MockBackend e relógio gravado, compara todos os snapshots físicos e correlaciona cada frame aos componentes, ganho e efeitos da captura principal. Assim, o trace de outra sessão não passa apenas por ter JSON válido. Footers ausentes permanecem incompletos; uma falha de dispositivo real pode tornar o último trecho não reproduzível e nunca é classificada como ensaio físico bem-sucedido.

O diagnóstico `force_pipeline` discrimina ganho, teto, slew, rate gate e perdas relativas. F10 e o trace também incluem `io.gain_commanded`, `io.level_uploaded`, `io.level_commanded`, latências e taxas de uploads/play. O ganho comandado não é readback de firmware; ver [FFB_INTENSITY.md](FFB_INTENSITY.md).

## Validação realizada e limitações

**415 testes passando, zero falhas esperadas e zero ignorados**, com `scripts/test-offline.py` bloqueando opens reais de UHID/input/hidraw/USB. Cobrem ABI EVDEV fake, identidade/capacidades, único slot, intensidade, polaridade, slew, F9, zero, watchdog, comandos pendentes, processo do jogo, desconexão/exceções, teclado e replay pareado. Nenhum subprocesso EVDEV real ou ensaio de motor foi executado automaticamente; os testes físicos de 5% e 10% foram realizados pelo operador. Ganho/autocenter globais, exclusividade efetiva, USB, temporização kernel e teclas com o jogo em primeiro plano precisam de validação pelo operador.

As três capturas reais existentes passaram novamente em replay completo com equivalência semântica: 35.723, 37.280 e 54.648 ações. A terceira, `dirt5-y-20261024-144639.jsonl.gz`, contém 20.501 quadros com Y 0..255 e zero divergências entre ABS_Y e HID Y. Seu `mock_history_dropped=2216` refere-se ao histórico diagnóstico limitado a 4096 ações, não perda de comandos; a captura completa e seu replay permanecem íntegros. O relato de que o acelerador funcionou concorda com essa telemetria.

## Conferência manual do perfil normal

1. Conferir o perfil carregado: 100%/100%, polaridade −1, slew 327670 e `unlimited`. Não há redução automática de intensidade. Esperar `[PRONTO]` e selecionar o G29 no DiRT 5.
2. Usar o monitor readonly da GUI para conferir L1/R1, manche, D-pad e controles especiais. Códigos 300/301 compartilhados não são separados artificialmente; confirmar A/B do dial.
3. Iniciar saída somente por `--enable-ffb` ou pelo botão físico explícito da GUI. O programa não gera pulsos de teste; a força vem dos efeitos do jogo.
4. Conferir F7/F8 em 0..100%, F9, permanência desligada, `ffb-arm` explícito, Pause/Disable/Stop e saída do jogo. Verificar zero/erase e ausência de força residual. F10 registra nível, ganho e perdas; valores são comandos, não leitura de torque.
5. Se precisar de coleta, escolher `--capture` explicitamente; nenhum arquivo grande é criado por padrão. O workflow de perfis e GUI está em [PROFILES_AND_MAPPING.md](PROFILES_AND_MAPPING.md).

Não executar automaticamente os scripts históricos de pulsos. Não instalar drivers ou trocar para comandos Logitech proprietários. A validação física e visual é feita pelo operador; os testes automatizados permanecem offline.
