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

Resultado da etapa matemática: **95 testes: 89 passando, 6 falhas esperadas, nenhum teste ignorado e nenhuma falha inesperada**. Os 18 testes originais continuam presentes. Nesta etapa foram convertidos em testes normais direção, sinais dos coeficientes, saturação unsigned, envelope e cancelamento antes do clipping. O vetor de full scale negativo usa agora o Logical Minimum real −32768; o caso −32767 continua coberto independentemente, conforme justificado em `FFB_MATH.md`.

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

As seis falhas esperadas naquele ponto eram:

1. Output recebido por Set Report não usa o decoder completo.
2. Loop Count não repete efeitos finitos.
3. Start de efeito ativo não reinicia a timeline.
4. Pause/Continue não preserva o estado pausado.
5. Pool cheio sobrescreve blocos sem retornar Full.
6. Nível estável ainda provoca uploads redundantes.

Próxima etapa recomendada: corrigir transporte/ciclo de vida offline, incluindo pool, Start/Stop/Solo, loops e pausa com tempo congelado. Depois eliminar uploads redundantes sem perder a renovação do replay curto e ampliar o diagnóstico/replay. A integração física deve continuar aguardando decisão e aprovação próprias.

Ainda exigem validação física: polaridade entre ABS_X/nível EVDEV/sentido mecânico, referência de velocidade/aceleração e filtros, equivalência do atrito Coulomb ao firmware, faixa dinâmica/quantização efetiva, latência/taxa e expiração de replay. A reserva opcional muda a reprodução das condições e precisa ser avaliada conscientemente. O modo linear não promete transientes intactos quando a soma ultrapassa a capacidade física. O descritor unsigned de Ramp, Custom não implementado e ausência de captura completa de corrida permanecem limitações documentadas.

## Transporte e ciclo de vida concluídos

Estado atual: **168 testes passando; zero falhas esperadas, zero testes ignorados e zero falhas inesperadas**. Todos os 18 casos originais continuam presentes. As seis marcas restantes foram removidas após as respectivas correções, sem eliminar asserts. A comparação dos campos históricos de RAM nos CSVs passou a ser feita contra o código preservado; a nova resposta tem vetores exatos e orçamento coerente, em vez de repetir os valores fictícios do protótipo.

Foram adicionados `pid_decoder.py` (validação do contrato PID), `transport.py` (framing/respostas UHID), `pool.py`, `lifecycle.py` e `session.py`. A sessão é exclusivamente mock, sem fd, threads ou I/O de dispositivo. Os novos testes percorrem sequências completas e os 132 registros do corpus real, mantendo a distinção entre capturas e vetores sintéticos.

| Commit | Entrega |
|---|---|
| `ddeb455` | Pool determinístico e respostas de alocação/memória |
| `d5a58f6` | Repetições, Start/Stop/Solo, pausa e controle dos atuadores |
| `037a758` | Transporte UHID tipado, validação e deduplicação limitada |
| `eca759b` | Deduplicação de uploads, renovação separada e expiração mock |
| `dc0adf3` | Sessão e sequências de integração exclusivamente offline |
| `275a4af` | PID State de acordo com as usages repetidas do descritor |

Pronto para o próximo trabalho de integração: contratos de bytes, pool, timeline, matemática, mistura e scheduler de saída simulada. Ainda não pronto como bridge físico: não há leitor/escritor UHID/EVDEV novo nem integração ao launcher, notificações assíncronas de PID State ou implementação de Custom. A validação de protocolo não transforma o UHID em uma réplica completa do USB proprietário do G29.

O plano de conexão por adaptador, **sem execução nesta entrega**, está em [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md). Os detalhes de tipos, Loop Count zero/255, política de memória, idempotência e limites do mock estão em [PID_TRANSPORT.md](PID_TRANSPORT.md). Antes de qualquer backend físico, ainda serão necessárias captura completa de corrida e aprovação para os ensaios correspondentes.

## Runner passivo implementado

Estado atual: **220 testes passando, zero falhas esperadas e zero ignorados**. Foram adicionados 52 casos aos 168 anteriores, incluindo equivalência AST/bytes da entrada v0.8, ABI/erros UHID, captura completa em JSONL/gzip, replay semântico, tráfego simultâneo, encerramento inesperado e cleanup com falhas injetadas. A execução adicional por `scripts/test-offline.py` bloqueia opens reais de dispositivos com audit hook. Nenhum dispositivo foi aberto para validar esta etapa.

| Commit | Entrega |
|---|---|
| `023fb99` | Fachada v0.8 byte a byte, DFGT readonly e I/O UHID limitado |
| `da3b4ce` | Corridas longas exclusivamente mock e reset em CLOSE sem encerrar o dispositivo |
| `dc189cb` | Timestamps monotônicos por SYN_REPORT, configurados somente no leitor |
| `df41a52` | Captura versionada, fases, respostas/estado/forças e replay determinístico |
| `bbea3a7` | Latência de processamento/entrega separada do relógio reproduzido |
| `f8016c4` | Runner passivo, console/diagnóstico e entrypoints separados dos launchers |

Arquivos novos em `input/`, `uhid_io.py`, `passive.py`, `capture.py`, `replay.py` e `runner.py`; contratos do núcleo receberam apenas as opções de sessão mock e extensão de Input GET necessárias. Não se reescreveu o núcleo matemático nem referências históricas. Não há backend EVDEV físico, seleção de comandos Logitech ou opção para ativar motor.

Consulte [PASSIVE_RUNNER.md](PASSIVE_RUNNER.md) para comandos preparados e schema. Próxima tarefa: operador coletar corrida real passiva, confirmar identidade/controles/menu FFB e driver virtual, registrar Proton/rota/cenário e conferir replay completo. Probes não são identificáveis com certeza por UHID; fases são anotações humanas, com autoria conservada como desconhecida. Os CSVs antigos continuam insuficientes para validar corrida e a fixture nova de dez minutos é explicitamente sintética.

Permanecem pendentes hardware/Proton reais, associação efetiva de driver, aceitação das notificações PID State, escala/filtros de movimento, tipos/flags/Loop Count usados pelo jogo, latência sustentada e completude dos efeitos suportados. Só depois dessa coleta será revisado um adaptador físico, com limites, deadline independente, emergência/exclusividade e ensaio aprovado. Os launchers e configurações SteamOS permanecem inalterados.

## Regressão do acelerador identificada em capturas reais

As capturas `dirt5-passivo-01.jsonl.gz` e `dirt5-20261024-142711.jsonl.gz`, fornecidas pelo operador, passaram integralmente no replay antes das mudanças. Não foi necessário abrir dispositivos. A mais recente reproduz 21.554 eventos UHID e 12.731 quadros de entrada; a anterior corresponde aos 19.670 eventos UHID e 9.594 quadros relatados. Todos os 22.325 inputs coincidem com a v0.8 em Z, mas a configuração funcional informada era **Y**. Em ambas as capturas, ABS_Y varia 0..255 e HID Y fica fixo em 255.

O CLI agora escolhe Y por padrão e mantém opções explícitas. Foram adicionados testes dourados e 13 amostras reais com proveniência. Nenhuma modificação em descritor, referências ou `src/dfgt_bridge/ffb/`. Os 16 rejeitados de cada sessão são três vetores Logitech conhecidos: autocenter off (5), fixed loop off (10) e LEDs off (1). O novo diagnóstico os distingue de falhas PID/transporte sem executá-los e sem apagar erros brutos.

Estado: **241 testes passando, zero falhas esperadas e zero ignorados**, também com bloqueio de abertura real de dispositivos. Capturas, referências e launchers preservados. Replay com `--verify-compatible` verifica todos os resultados de capturas antigas apesar do novo hash dos fontes; o padrão continua estrito. Reutilização de nome de captura produz erro explicado com sugestão de outro nome, sem sobrescrever.

| Commit | Entrega |
|---|---|
| `719bee0` | Padrão Y no CLI e regressões de pedais com bytes reais/dourados |
| `e5e2089` | Erro de arquivo existente com nome alternativo sugerido |
| `445f3ae` | Classificação observacional dos pacotes Logitech e telemetria dos pedais |
| `8644190` | Replay entre versões com equivalência semântica integral obrigatória |

Análise, índices individuais de todos os rejeitados, hashes, fórmulas de entrada e comando atualizado estão em [CAPTURE_COMPARISON_20261024.md](CAPTURE_COMPARISON_20261024.md). Próxima tarefa: o operador repetir o teste passivo em Y, marcar menu/corrida e conferir o acelerador no jogo. A ausência de Start em Damper/Friction explica seus componentes zero nas capturas; não constitui falha matemática. Backend físico continua fora desta etapa.

## Integração EVDEV supervisionada implementada, sem ensaio de motor

O operador confirmou o acelerador. Foram conferidas novamente, apenas por replay offline, as três capturas disponíveis: 35.723, 37.280 e 54.648 ações, todas completas, sem divergências e sem erros de entrega. A terceira (`dirt5-y-20261024-144639.jsonl.gz`, SHA-256 `b01a5916e56885cc134c30108760c6e358f9d60f00811afc9d0714580506b75d`) contém 20.501 quadros em Y, extremos 0..255 e zero divergências ABS_Y→HID Y. As capturas e referências não foram alteradas.

Estado: **307 testes passando, zero falhas esperadas e zero ignorados**. São 66 casos novos sobre os 241 anteriores: ABI EVDEV fake, identificação e capacidades, ausência de writer no modo passivo, slew/teto/polaridade, watchdog, F9, perda de dispositivo, teclado, saída/reutilização do PID do jogo, IPC limitado, emergência e replay pareado. A suite passou com audit hook que impede abertura real de dispositivos. Nenhum motor ou subprocesso EVDEV real foi iniciado.

| Commit | Entrega |
|---|---|
| `a03213c` | Backend EVDEV DFGT-only com opt-in, validação, único slot e cleanup |
| `3f2c90b` | Política supervisionada: cálculo fresco, slew, latch e duração limitada |
| `de8844a` | Teclas globais F7..F10 readonly, sem grab e com fallback de emergência |
| `7ab5f65` | Processo separado, IPC com um pendente/último valor, diagnóstico e replay físico |
| `8b815a7` | Supervisão da saída do jogo e invalidação de frames anteriores a Stop/zero |
| `7fb741f` | Testes de integração, falhas, processo do jogo e correlação do trace físico |

Novos módulos: `backends/evdev.py`, `supervised.py`, `physical_worker.py`, `physical_replay.py`, `game_monitor.py` e `input/global_keys.py`. `runner.py` ganhou flags e comandos opt-in; `replay.py`, verificação pareada. Em `ffb/safety.py`, somente uma opção de slew em unidades/s foi adicionada: seu padrão conserva o comportamento dos replays antigos. Descritor, fontes de entrada, renderer, mixer, decoder, referências e launchers permanecem inalterados.

O perfil físico começa em 10%, usa teto padrão de 20% (configurável até 30%), watchdog independente de 100 ms, replay EVDEV máximo de 160 ms e duração supervisionada de 120 s por padrão. F9 nunca religa; requer `ffb-arm`, jogo ativo e cálculo posterior. A saída do DiRT5.exe é monitorada separadamente de UHID_CLOSE. A política física recebe componentes antes do teto mock, evitando aplicar o ganho duas vezes.

Ver [SUPERVISED_FFB.md](SUPERVISED_FFB.md) para comandos e procedimento manual. Permanecem pendentes validação de polaridade mecânica, prazo real kernel/USB/firmware, captura global de teclado no jogo e exclusividade efetiva. O flock é cooperativo; FF_GAIN/AUTOCENTER são globais, ficam zero ao encerrar e não têm restauração de estado desconhecido. Não houve instalação de driver, modificação do SteamOS ou push. O próximo passo é o ensaio supervisionado manual a 5%, não um teste automático de motor.


## Correção da regressão de enumeração PIDFF

Estado: **346 testes passando, zero falhas esperadas e zero ignorados**. Os 39 novos testes cobrem propriedade UHID por PHYS, preservação de serial/descritor/controles, associação simulada e seus erros, ACK durante bind bloqueado, cancelamento, timeout, perda de entrada/capacidades, proteção dos dispositivos físicos e reenvio dos pedais sem amostragem artificial. A suite passou com audit hook que bloqueia dispositivos reais e ResourceWarning tratado como erro.

A v0.8 carregava PIDFF e registrava o ID G29 em uma thread enquanto atendia UHID; esse mecanismo foi omitido no runner inicial. Foi restaurado com validação do nó próprio e prontidão obrigatória (PIDFF, EVDEV e FF_CONSTANT). O processo físico permanece adiado até essa verificação, mesmo com opt-in. STOP durante a associação não encerra prematuramente a sessão; após pronto, continua terminal.

Commits da implementação: `2f0828d` propriedade PHYS sem mudar serial; `d9ff562` ciclo de vida do bootstrap; `d0e904e` associação guardada e capacidades; `4f60e36` integração e prontidão; `6fbba10` reenvio de controles sem nova amostra. Referências e launchers não foram modificados.

Nenhum módulo foi carregado, nenhum bind real foi escrito e nenhum motor foi acionado. O módulo PIDFF instalado e seu estado não registrado foram inspecionados somente em leitura. A API HID exige fallback global por VID/PID quando falta uma regra; não permite um override por instância. Outro G29 presente bloqueia esse fallback, mas a regra pode afetar G29 conectado posteriormente e permanece até unload/reboot. Não há instalação ou configuração persistente. Diagnóstico, fontes e comando passivo preparado: [PID_BINDING.md](PID_BINDING.md).

Próxima tarefa: operador confirmar enumeração, controles e menu FFB no SteamOS em modo passivo antes de retomar o ensaio físico supervisionado. Não houve push.


## Intensidade física 0..100% e auditoria de atenuação

O operador realizou ensaios reais de 5% e 10% e relatou força quase imperceptível. A faixa agora aceita 0..100% em CLI, política supervisionada e IPC, inclusive máximo zero. O backend valida nível signed nativo e a ABI é testada até os extremos. F7/F8 continuam em passos de 5%, limitados ao máximo explicitamente escolhido. Padrões conservadores de 10%/20% foram mantidos; as antigas restrições de 30% e 70% foram removidas.

O ganho do mock principal não entra no caminho físico: um teste integrado HID→renderer→supervisor→ABI EVDEV comprova nível 32767 e FF_GAIN=65535 com o mock ainda a 20%. Não foi adicionado boost. O slew de 16383,5 unidades/s era fixo e atenuava transientes; agora é configurável sem teto artificial por `--ffb-slew-rate`. O comando de diagnóstico usa 327670 unidades/s.

F10/status/trace discriminam intensidade, ganho efetivo, teto, força antes/depois das proteções, perdas relativas, direção, updates/s e o ganho/nível realmente comandados pelo backend EVDEV. Os novos traces v2 exigem diagnóstico equivalente no replay; v1 conserva a comparação de todos os campos originais. O histórico temporal de diagnóstico é limitado e não forma fila de força.

Estado: **375 testes passando, zero falhas esperadas e zero ignorados**, com relógios/dispositivos falsos e audit hook de abertura de dispositivos. Novos casos cobrem 0, 10, 30, 50, 75, 100%, máximo zero, parâmetros inválidos, ajuste durante execução, F7/F8/F9, zero imediato, slew, F10, magnitude nativa, ausência de ganho duplicado, taxa física e replay de telemetria assinada.

Os pares reais `dirt5-fisico/policy-20261024-211132` e `...223235` passaram no replay completo correlacionado: 35691/11992 e 28793/10116 steps/frames físicos. Tinham prazo configurado de 30 s e atingiram picos de 1621 e 3147. Reprocessar o ensaio de 10% a 100% com slew de 327670/s alcançou 32767 no mock, em vez de 26172 com o slew antigo. A desmontagem somente leitura dos módulos instalados confirmou o deslocamento de resolução em ff-memless e o comando de oito bits do lg4ff; não confirmou torque, alimentação ou exclusividade de outros escritores.

Implementação: `6beb151` libera a faixa e configura slew; `481464e` acrescenta diagnóstico por etapa e replay compatível; `0052a40` verifica F10 e configuração do processo. Relatório, hashes e comando pronto: [FFB_INTENSITY.md](FFB_INTENSITY.md). A próxima tarefa é o operador conferir força/magnitude/ganho em 100%; fidelidade dos efeitos fica para depois. Nenhum motor, carregamento/instalação de driver, alteração do SteamOS, referência histórica ou launcher foi executado/modificado pelo agente; sem push.


## Defaults finais e GUI de mapeamento (2026-10-25)

Defaults oficiais centralizados: **100%/máximo 100%, polaridade −1, slew 327670 e duração ilimitada**, acelerador Y, G29 e auto PIDFF. Não foi imposto outro teto inferior. O opt-in físico continua obrigatório. A sessão ilimitada retira só o timer global; watchdog de cálculo e prazo EVDEV finito permanecem independentes, assim como F9, falhas e cleanup.

`input/mapping.py` agrega borboletas/manche em L1/R1, +/− em hat, buzina em + virtual, clique em Enter, dial nos destinos próprios e Guide em PS. Preserva bytes dos controles ordinários e slots adicionais legados; 300/301 compartilhados pelo corpo/manche são declarados como tal. `report()` e descritor não mudaram. A configuração registrada em captura reproduz o mapa exato, sem alterar capturas antigas.

`config.py`/ProfileStore oferece JSON versionado por jogo para CLI/GUI, validação e escrita atômica. `mapping_editor.py` mantém histórico limitado, conflitos explícitos e prévia. `gui.py`, `wheel_art.py` e SVGs originais CC0 implementam vistas lado a lado, hotspots de todos os botões, detecção readonly, pulsos curtos visíveis sem latch, remapeamento/eixos, presets, salvar/carregar e desfazer/refazer. A GUI só inicia o runner por ação explícita; EOF de seu canal encerra o filho. Nenhuma captura grande é criada por padrão.

**415 testes passando**, sem falhas esperadas/ignorados, com dispositivos/widgets/processos falsos e audit hook. Preservamos os vetores de ensaios anteriores com configurações explícitas em seus fixtures; os defaults novos têm testes próprios, inclusive ABI EVDEV signed invertida e mais de 500 s simulados com watchdog. Dois pares reais antigos continuaram completos no replay correlacionado. Não houve motor, drivers ou configuração do SteamOS executados/alterados pelo agente; sem push.

Os SVGs foram renderizados offline. Não houve conexão ao display :0 para validar a janela real; Tk callbacks foram exercitados com simuladores. O build isolado em /tmp encontrou setuptools ausente e não instalou nada. A validação interativa no SteamOS, sentido A/B do dial e navegação das abas pelo L1/R1 permanecem tarefas do operador, sem execução física automática. Recursos de pacote/entrypoints estão declarados em pyproject.toml; execução pelo fonte permanece disponível.

Commits: `4c35cbd` mapper/agregação e replay de entrada; `0f22256` perfis/defaults/duração; `8ccad90` GUI/arte/controles; `8f76df2` pulsos curtos. Comandos e limites: [PROFILES_AND_MAPPING.md](PROFILES_AND_MAPPING.md).


## Integração automática Steam/systemd (2026-10-25)

Implementados `steam_protocol.py`, `steam_service.py`, `steam_launcher.py`, `steam_install.py` e bootstrap `steam_main.py`, com wrappers install/uninstall/Steam e opção na aba Geral. O root executa somente snapshot protegido do backend; o launcher e Steam/Proton permanecem no usuário instalado. IPC fechado autentica credenciais, valida perfil, mantém lease e exclusividade. Prontidão exige PIDFF/EVDEV/FF e confirmação do worker, sem alterar descritor ou matemática. O runner manual permanece passivo por padrão.

**459 testes passam**, zero falhas esperadas/ignorados, incluindo autorização, lifecycle, argumentos, heartbeat, duas sessões, reinício e instalador repetível em diretório temporário. Shell e dry-run passaram. O sandbox impediu IPC Unix nativo e verificação systemd; nenhuma unidade foi instalada/iniciada. A confirmação operacional no SteamOS ainda é necessária. Perfil 100%/100%, −1, slew 327670 e ilimitado foi preservado conforme validação física informada pelo usuário; não houve motor executado pelo agente.

Commits: `79c2760` prontidão e escopo do usuário; `4eb6549` serviço autenticado e progresso do renderer; `cfbcf5e` launcher, instalação e GUI. Procedimentos completos e limites estão em [STEAM_INTEGRATION.md](STEAM_INTEGRATION.md). Referências históricas e capturas foram preservadas; sem push.

## Erro 13 na ativação Steam (2026-10-25)

Separado RuntimeDirectory root (`dfgt-bridge-backend`, 0700) da árvore do socket
0600 do usuário. A configuração anterior permitia que systemd alterasse o dono
do socket ao iniciar o daemon. Logs locais e metadados sustentam esse mecanismo;
o erro antigo não tinha contexto suficiente para provar a syscall original.
Diagnósticos agora incluem operação, caminho abreviado quando privado e errno.
O perfil já era carregado pelo launcher e revalidado no serviço; preservamos IPC
fechado, home inacessível, isolamento e defaults aprovados. A atualização confere
locks antigos e novos e recria o socket por reinstalação das unidades.

468 testes offline passam. Novos casos cobrem home bloqueado simulado, cópia
privada 0600, permissões, cleanup, árvores systemd distintas, migração e USB com
novo fd pelo mesmo by-id. Referências, descritor e matemática não mudaram.
Procedimento de atualização e dois probes passivos em STEAM_INTEGRATION.md.
Nenhum serviço/dispositivo/motor foi iniciado; validação nativa permanece com o
operador.
