# Auditoria FFB DFGT → G29 virtual — primeira entrega

Data: 24/10/2026. Escopo: análise dos fontes e capturas, consulta de fontes primárias e testes offline. Nenhum script de operação foi modificado. Nenhum teste físico, bind/unbind, upload EVDEV, escrita em hidraw ou ajuste de kernel foi executado.

## Conclusão técnica

**A tradução atual não é fiel. Não há evidência de que os efeitos mencionados do DiRT 5 exijam algum recurso exclusivo do G29 que impossibilite FFB no DFGT.** Há limitações de driver, resolução e resposta física, além de erros concretos de semântica no bridge.

São três interfaces diferentes:

1. DiRT 5/DirectInput/Wine/Proton e a interface apresentada ao jogo.
2. Relatórios HID PID definidos pelo descritor do nosso dispositivo UHID.
3. EVDEV do DFGT → driver Logitech → comandos proprietários do volante.

Os relatórios `0x50..0x5d` dos scripts vêm do descritor PID que o bridge anuncia. **Não são uma captura do protocolo USB proprietário de um G29 físico.** Usar `046d:c24f` permite reconhecimento, mas não torna o descritor uma réplica do hardware. Também não há captura USB de um G29 físico nesta pasta.

O driver principal do Linux atribui ao DFGT e ao G29 a mesma lista básica de efeitos e o mesmo caminho de força constante. Isso demonstra compartilhamento desse caminho, sem justificar copiar indiscriminadamente comandos de configuração, mudança de modo ou outros relatórios entre os modelos. [Fonte: hid-lg4ff do Linux](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-lg4ff.c).

## Evidências locais e limites da verificação

| Item | Observação nesta sessão |
|---|---|
| Kernel em execução | `7.2.7-valve2-2-neptune-72-g31504582a073`, x86_64, PREEMPT_DYNAMIC |
| Dispositivo físico no sysfs | `0003:046D:C29A.000E`, nome `Driving Force GT` |
| Driver associado | `/sys/bus/hid/drivers/logitech`; módulo `hid_logitech` |
| Bitmap FF | `300040000 0` em `/sys/class/input/event17/device/capabilities/ff` |
| Decodificação do bitmap, palavras de 64 bits | `FF_CONSTANT=0x52`, `FF_GAIN=0x60`, `FF_AUTOCENTER=0x61` |
| Efeitos condicionais via EVDEV atual | Não anunciados: Spring, Damper, Friction e Inertia |
| Faixa física no sysfs | `range=900`; somente leitura |
| Infraestrutura carregada | `ff_memless`, `uhid`, `hid_universal_pidff` e `hid_logitech` |
| Módulo Logitech | Arquivo em `kernel/drivers/hid/hid-logitech.ko.zst`; srcversion `B245F98369C6B49CC88E4EE` |

O ambiente expõe o sysfs, mas não disponibilizou `/dev/input/event17`. Portanto, não foi possível consultar `EVIOCGEFFECTS` nem `EVIOCGABS`. Número de slots EVDEV físicos, latência USB, ganho/autocenter efetivamente aplicados e resposta mecânica continuam sem medição. O número 40 anunciado pelo UHID não é uma medição de slots físicos.

Não encontrei o fonte exato desse kernel Valve disponível localmente. A comparação de implementação usa os projetos upstream consultados na data acima; não constitui prova de identidade binária com o módulo instalado. O bitmap e o vínculo observados são evidência direta da interface atual.

`FFB_AUDIT_MANIFEST.json` registra hashes dos arquivos preexistentes, inventário das capturas e observações do sistema. Não há metadados Git utilizáveis neste workspace para estabelecer histórico de commits.

## Inventário dos scripts e preservação dos controles

| Família/versão | Papel e resultado da inspeção |
|---|---|
| `dfgt_bridge*` | Experimentos anteriores com gamepad Xbox/uinput; não constituem o caminho PID atual |
| `dfgt_g29_probe_v01/v02` | Identidade G29 em uinput; sem emulação HID completa de FFB |
| UHID v0.1–v0.4 | Evolução do descritor de entrada, associação e observação passiva |
| UHID v0.5/fix–v0.7/fix | Inclusão PID, handshake e associação experimental; v0.7 também experimenta G920 |
| **UHID v0.8** | Referência funcional: Automobile Simulation Device para classificação de volante; FFB passivo |
| v0.9 | Decodifica somente Constant e mistura em um FF_CONSTANT físico |
| v0.10 | Acrescenta boost; mantém as limitações do decoder da v0.9 |
| v0.11 | Sintetiza Spring por posição; força o sinal de retorno |
| v0.12 | Acrescenta Sine e Damper, estimativa de velocidade e limites por família |
| v0.13 | Amplia as famílias, duração/envelope aproximados, hotkeys e diagnóstico |
| `dfgt_ffb_physical_test_v01..v03` | Geram força física; foram inspecionados, sem execução |
| `launch-dirt5.sh`, `test-180.sh` | Ajustam rotação e chamam o bridge Xbox antigo; não foram executados |

Comparação AST: `descriptor()`, `report()`, `create_device()` e `send_input()` são iguais da v0.8 até v0.13. Os testes também comparam os bytes completos do descritor e vetores de entrada, incluindo pedais, hat, botões e opções de acelerador. Isso preserva o contrato de entrada em análise estática; não substitui validação dentro do jogo.

SHA-256 da v0.8 preservada: `630d78fa050f91acdc443f40ad4ac78be1b0dcf1c750f87dc0fbe5ac9e1f429a`.

## Drivers estudados

| Projeto | Constatação relevante | Consequência para este bridge |
|---|---|---|
| `hid-lg4ff` upstream | Anuncia Constant e Autocenter; `ff_memless` fornece ganho e gerenciamento em software | O EVDEV atual permite usar torque constante como backend sem trocar o driver |
| `new-lg4ff` | Suporta DFGT; organiza força sintetizada e condições em quatro slots de firmware. DFGT tem flag de Friction. Inertia é convertida em Damper | A limitação observada no EVDEV não descreve todo o firmware; Inertia continua sendo aproximação |
| `lg4ff_userspace` | Port incompleto para hidapi/uinput; o autor informa que DFGT foi testado com um G29 em modo DFGT | Referência útil, sem comprovação de funcionamento deste DFGT físico |

Fontes: [README new-lg4ff](https://github.com/berarma/new-lg4ff#readme), [implementação new-lg4ff](https://github.com/berarma/new-lg4ff/blob/master/hid-lg4ff.c), [README lg4ff_userspace](https://github.com/Kethen/lg4ff_userspace#readme).

No código de `new-lg4ff`, os tipos de firmware usados incluem Constant `0x00`, Spring `0x0b`, Damper `0x0c` e Friction `0x0e`; início/atualização/parada usam operações de slot. Periodic e Ramp são calculados em software e somados ao canal constante. O caminho Constant do driver Linux básico usa outra codificação (`0x11,0x08,...`; neutro `0x80`, parada `0x13`). **Esses bytes não são um tutorial de transmissão nem devem ser enviados pelo backend EVDEV.** A quantização final de força é de aproximadamente oito bits nesses caminhos. [Fontes: new-lg4ff](https://github.com/berarma/new-lg4ff/blob/master/hid-lg4ff.c), [hid-lg4ff](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-lg4ff.c).

Não adotaria `lg4ff_userspace` sem revisão: `lg4ff_init_slots()` constrói comandos nos slots, mas o loop chama `hid_write(..., cmd, 7)` com o buffer local anteriormente preparado, em vez de `current_cmd`. Há ainda apenas um agregado por família condicional, sujeito a sobrescrita entre efeitos. São achados de código, sem execução física. [Fonte: force_feedback.c](https://github.com/Kethen/lg4ff_userspace/blob/main/force_feedback.c).

## Compatibilidade dos efeitos

“Disponível no firmware” abaixo descreve o caminho implementado pelos drivers estudados, sujeito às limitações indicadas; não significa que foi ensaiado fisicamente nesta sessão. A relação de efeitos usados pelo jogo foi informada pelo usuário e ainda precisa de captura completa local.

| Efeito recebido no PID virtual | EVDEV físico atual | Caminho DFGT possível | Situação da v0.13 |
|---|---|---|---|
| Constant, tipo 1 | `FF_CONSTANT` | Nativo pelo driver atual | Nível signed é lido, mas direção é ignorada |
| Spring, tipo 9 | Não exposto | Condição de firmware ou síntese por posição | Síntese com sinal forçado, escala de saturação errada e teto por família |
| Damper, tipo 10 | Não exposto | Condição de firmware ou síntese por velocidade | Velocidade filtrada, limiar e escala empíricos; não equivale automaticamente ao nativo |
| Friction, tipo 12 | Não exposto | Condição de firmware no DFGT ou síntese | Lei de resistência depende de velocidade arbitrariamente; perde sinal dos coeficientes |
| Sine, tipo 5 | Não exposto | Síntese no host → Constant | Implementada; sujeita a baixa taxa, fase/direção e envelope |
| Square/Triangle/Saw, tipos 4/6/7/8 | Não expostos | Síntese no host → Constant | Implementadas sem validação por captura de corrida; fases precisam de vetores de referência |
| Ramp, tipo 2 | Não exposto | Síntese no host → Constant | Descritor unsigned de 8 bits perde representação signed direta; direção também ignorada |
| Inertia, tipo 11 | Não exposto | Síntese por aceleração; new-lg4ff aproxima como Damper | Síntese experimental com escala/filtro empíricos |
| Custom, tipo 3 | Não exposto | Exigiria implementação específica | Anunciado no descritor, mas ignorado pelo renderer |
| Autocenter | `FF_AUTOCENTER` | Recurso separado do firmware | Não substitui o Spring enviado pelo jogo; interação com o estado físico não é gerenciada |

A escolha conservadora para a próxima implementação é manter EVDEV/Constant e corrigir primeiro a semântica PID. Um backend condicional nativo pode ser avaliado depois, isoladamente, com aprovação. Não é necessário trocar o kernel para começar.

## Mapa dos relatórios do descritor atual

Offsets incluem Report ID no byte 0. Estes IDs são específicos deste descritor, não números universais de protocolo Logitech. A interpretação foi conferida pelos itens HID locais e pelo código do decoder.

| Report ID e tipo | Campos declarados | Tratamento atual / ação necessária |
|---|---|---|
| `0x50` Output | Device Control, seletor no byte 1 | Separa Reset, Stop All, Enable/Disable e Pause/Continue de forma incompleta |
| `0x50` Input | Estado PID e índice | Declarado, mas não publicado por este bridge; implementar estado coerente |
| `0x51` Output, 18 bytes | Bloco 1, tipo 2; duração/repetição/amostragem/delay em 3/5/7/9; ganho 11; trigger 12; flags 13; direções 14/16 | Offsets principais estão corretos. Direção armazenada sem uso; flags, trigger e sample period ignorados |
| `0x52` Output, 10 bytes | Attack/Fade Level em 2/4; Attack/Fade Time em 6/8 | Decoder lê a ordem certa; cálculo transforma níveis absolutos em fatores multiplicativos |
| `0x53` Output, 15 bytes | Bloco 1, offset 2, centro/coeficientes signed em 3/5/7; saturações/deadband unsigned em 9/11/13 | Eixo 0 considerado; escala unsigned e semântica dos coeficientes não preservadas |
| `0x54` Feature, 4 bytes | Create New Effect, tipo 1 e tamanho | Aloca circularmente sem verificar ocupação; sempre promete sucesso |
| `0x55` Output, 4 bytes | Constant: magnitude signed em 2 | Leitura do nível correta; falta projeção da direção |
| `0x56` Feature, 5 bytes | Block Load: índice, status, RAM disponível | Sempre Success e `0xffff`; sem memória/ocupação reais |
| `0x57` Feature, 5 bytes | Pool: RAM, simultâneos, flags | Anuncia 40 efeitos; precisa corresponder ao gerenciador virtual |
| `0x58` Output, 4 bytes | Ramp Start/End unsigned, 8 bits | Mapeia 0..255 para 0..32767; não representa diretamente os dois sinais |
| `0x59` Output, 2 bytes | Device Gain unsigned 0..255 | Normalização /255 correta; não confundir com ganho por efeito /100 |
| `0x5a` Output, 4 bytes | Bloco, Start/Start Solo/Stop, Loop Count | Stop e Solo parciais; loops ignorados e Start ativo não reinicia timeline |
| `0x5b` Output, 2 bytes | Block Free | Remove no caminho Output e Set Report; alocador não reutiliza explicitamente o bloco livre |
| `0x5d` Output, 10 bytes | Magnitude unsigned em 2, offset signed em 4, phase em 6, period em 8 | Magnitude é lida como signed apesar do intervalo não negativo; valores válidos até 32767 não mudam por isso. Falta direção |

Tempos declaram expoente −3 (milissegundos); ângulos declaram −2. Phase/Direction têm máximo lógico **35900** neste descritor, enquanto o renderer divide a fase por **36000**. A diferença é pequena, mas a normalização precisa ser única e refletir a conversão efetiva do host. Duração infinita usa `0xffff`; a distinção entre zero e infinito também precisa de teste. O descritor admite 12 tipos de efeito, mas declara Logical Maximum `0x12` (=18) para o seletor: convém restringir os valores reservados sem alterar a entrada.

O caminho `SET_REPORT` só trata Create/Free/Gain, enquanto `OUTPUT` passa pelo decoder completo. A mesma semântica de Output precisa funcionar nos dois transportes. O driver PID do Linux escala campos segundo os limites lógicos, inclui direção e converte os tempos; é uma referência para desfazer essas conversões. [Fonte: hid-pidff](https://github.com/torvalds/linux/blob/master/drivers/hid/usbhid/hid-pidff.c).

## Divergências reproduzidas e prioridade

As primeiras 13 linhas abaixo têm testes de divergência explícitos. Os números são exemplos sintéticos, separados dos CSVs reais.

| Prioridade | Divergência | Evidência / consequência |
|---|---|---|
| P0 | Redução do teto pode ser violada pelo slew | Com saída estabelecida em 60%, reduzir para 5% permite que `last_sent` permaneça acima de `cap`; falta clamp final após slew |
| P1 | Watchdog de tráfego corta efeitos válidos | Constant infinito de nível 16384 vira zero após 201 ms, sem Stop. Condições estáveis também não precisam ser reenviadas continuamente |
| P1 | Coeficientes signed viram módulo | Trocar +32767 por −32767 não muda a força de Spring; F6 troca um sinal global, sem restaurar a informação perdida |
| P1 | Saturação unsigned é usada como força signed | Saturação 16384/65535, aproximadamente 25%, resulta em até 16384/32767, aproximadamente 50% |
| P1 | Direção é ignorada | Constantes com direções opostas produzem o mesmo sinal; pode eliminar cancelamentos esperados |
| P1 | Pause elimina `running` | Continue não retoma os efeitos; é preciso separar tempo pausado, habilitação dos atuadores e estado de reprodução |
| P1 | Output via Set Report é descartado | Atualização Constant enviada por esse transporte não altera o nível |
| P1 | Loop Count ignorado | Efeito de 50 ms com três execuções já está zerado aos 75 ms |
| P1 | Start de efeito ativo não reinicia | `started_at` fica antigo; a própria suíte embutida exige isso, consolidando uma escolha de implementação sem captura comprobatória |
| P1 | Envelope altera amplitude incorretamente | Constant 16384 com Attack Level 8192 começa em 4096, em vez do nível de ataque 8192 |
| P1 | Pool sobrescreve efeitos | A 41ª criação volta ao bloco 1, com sucesso, mesmo que todos estejam ocupados |
| P1 | Clipping antes da mistura destrói cancelamento | Duas constantes +32767 e Spring −32767 deveriam somar +32767; o clamp por família transforma isso em zero no renderer |
| P2 | Upload redundante | Mesmo nível estável provoca novo upload/play a cada tick, antes de vencer o replay |

A especificação PID define condições em função de posição, velocidade ou aceleração e preserva os coeficientes. Pause/Continue mantêm a reprodução suspensa; não equivalem a Stop All. Ela também define contagem de repetições e o controle separado dos atuadores. [Fonte: USB PID 1.0, seções 5.3, 5.9 e 5.13](https://www.usb.org/sites/default/files/pid1_01.pdf).

Outras limitações observadas, ainda sem medição física:

- No padrão de 20% de intensidade, o teto de Spring é 35% desse teto, ou **7% da escala EVDEV**; Damper fica em **3%**. São limites impostos pelo bridge, não capacidades do motor. Boost maior chega a esses tetos mais cedo.
- Friction tem teto de 15% e Inertia de 10% do teto global. Os contadores não registram esses cortes nem o clamp prévio por família; `saturation_ticks` observa apenas parte do clipping.
- A taxa nominal máxima é 45 Hz. Uma senoide de 50 ms, como o exemplo embutido, recebe no máximo 2,25 amostras por ciclo; acima de 22,5 Hz há risco de aliasing mesmo no caso ideal. `poll(10)` e o gate por intervalo podem diminuir a taxa efetiva. Isso requer medição, não simplesmente remover o limite.
- Filtros de 45 ms, limiares de velocidade, divisores empíricos e slew modificam os transientes. Damper/Inertia/Friction não usam centro e deadband de forma completa.
- O replay de 160 ms é controlado pelo kernel `ff_memless`; não é um watchdog autônomo comprovado no firmware. Ele protege contra parada do processo enquanto o kernel continua operante, sujeito a agendamento. [Fonte: ff-memless](https://github.com/torvalds/linux/blob/master/drivers/input/ff-memless.c).
- F9 desliga/religa, sem trava de emergência dedicada; F7 não chega a 0%. Não há controle alternativo se o teclado não for acessível. Ctrl+C depende do loop, e SIGTERM não tem tratamento explícito de limpeza.
- `--grab` controla entrega de eventos de entrada; não garante exclusividade de escrita FFB/hidraw. Outros clientes podem modificar ganho, autocenter e forças.
- `--auto-pidff` executa modprobe e escreve um ID dinâmico que corresponde ao VID/PID, não somente à instância UHID. Há verificações prévias de instância, mas o ID continua registrado até descarregar o módulo/reiniciar; não há remoção correspondente na v0.13.
- O modo passivo captura bytes, mas não arma o gerenciador de efeitos. É apropriado para coleta bruta; um diagnóstico de estado sem motor deve poder renderizar para um backend nulo.

## Capturas reais disponíveis

| Arquivo | Registros além do cabeçalho | Conteúdo |
|---|---:|---|
| `ffb-corrida-20261024-035050.csv` | 31 | `f5`, `0d`, `f8` |
| `ffb-pid-20261024-040456.csv` | 0 | Só cabeçalho |
| `ffb-pid-fix-20261024-040657.csv` | 19 | Comandos anteriores, Reset e Gain |
| `ffb-pid-fix-20261024-041217.csv` | 58 | Comandos anteriores, Reset e Gain |
| `ffb-pid-v06-20261024-042712.csv` | 24 | Pool/Create/Block Load/Free de inicialização, Reset/Gain e comandos anteriores |
| **Total** | **132** | **Nenhum Set Effect/Condition/Constant/Periodic/Effect Operation completo de corrida** |

Portanto, é possível testar o handshake e as sequências existentes, mas **não afirmar validação dos efeitos de corrida com esses arquivos**. Os exemplos no `--self-test` são chamados de capturas reais nos comentários, mas o trecho de Sine não aparece nos CSVs locais; sua origem não é verificável aqui. A anotação de “~12% gain” desse exemplo também não combina com o byte de ganho 100 nele presente.

Uma futura coleta passiva deve registrar início/fim, versão e opções do bridge, descritor/hash, versão Proton/Wine, rota HIDRAW/EVDEV utilizada, relógio monotônico, direção física com timestamps e cenário de jogo. Para avaliar Spring/Damper não bastam bytes PID sem posição/velocidade. Captura passiva não significa reprodução física; a execução do jogo com coleta fica para a etapa autorizada pelo usuário.

Wine pode converter efeitos para EVDEV; a origem exata de cada relatório do UHID depende da rota e da versão utilizada. Os CSVs atuais não permitem atribuir cada pacote diretamente ao executável do jogo. [Fonte de referência Wine, não prova da versão Proton local](https://github.com/wine-mirror/wine/blob/master/dlls/winebus.sys/bus_udev.c).

## Arquitetura proposta

```text
DFGT entrada ── input_adapter_v08 ── UHID Input Report 0x01 ── jogo
                                      │
UHID OUTPUT / SET_REPORT / GET_REPORT  │
              │                       │
         pid_transport                │
              │                       │
         pid_decoder ── comandos tipados e normalizados
              │
         effect_manager ── pool, timeline, loops, pause, enable, gain
              │
         force_renderer ← motion_estimator ← entrada com timestamp
              │
         gain_and_safety ← controles ao vivo / emergência
              │
         backend físico EVDEV Constant  OU  NullBackend para testes
              │
         telemetria/captura/replay offline em cada fronteira
```

Módulos previstos, a extrair incrementalmente, mantendo a v0.8 preservada:

- `pid_transport`: normaliza os dois caminhos de Output; responde features; valida tamanhos/tipos antes de ACK; não escreve no volante.
- `pid_decoder`: conhece o descritor, limites, sinais, unidades, flags e direção. Retorna comandos imutáveis; desconhecidos ficam diagnosticados e não viram força por adivinhação.
- `effect_manager`: pool real de efeitos virtuais, alocação/liberação, duração finita/infinita, Start/Stop/Solo, loops, pausa com tempo congelado e habilitação de atuadores. Nunca sobrescreve efeito ocupado silenciosamente.
- `force_renderer`/`motion_estimator`: soma em precisão ampla e projeta no eixo de direção. Posição/velocidade/aceleração usam timestamps de entrada e escala documentada. Filtros e aproximações são explícitos e testáveis. O normalizador não fixa a entrada mecânica em 180° ou 900° sem metadados.
- `gain_and_safety`: separa ganho solicitado pelo jogo, ganho do usuário e teto de saída. O teto final é aplicado **depois** do slew; reduções e emergência têm prioridade imediata. Ganhos separados por família ficam como ajustes opcionais, com perfil de referência sem compressão arbitrária.
- `physical_backend`: inicialmente EVDEV Constant, um slot reutilizado, dono único do dispositivo e confirmação de VID/PID/capacidade. Não contém decoder PID. Backend nativo futuro fica em implementação alternativa, nunca concorrente.
- `diagnostics`/`NullBackend`: replay determinístico com relógio falso; bytes, estado, torque pedido/aplicado e motivos de descarte. Nenhuma dependência de dispositivos nos testes de decoder/renderer.

O loop aplica eventos de estado em ordem. Atualizações de parâmetros podem ser consolidadas entre amostras, mas Create/Free/Start/Stop/Reset não podem ser descartados como se fossem níveis de força intercambiáveis. A saída mantém somente a amostra mais recente e não acumula uma fila de torques atrasados. Ao perder o prazo, registra a perda e calcula o instante atual, sem rajada para “alcançar” amostras antigas.

Uploads só ocorrem quando o nível quantizado/configuração muda. Renovações necessárias ao replay curto são separadas dos uploads e antecipadas à expiração. Não eliminar keepalive inadvertidamente ao deduplicar comandos. A frequência inicial e seus aumentos devem ser justificados por latência e carga medidas, preservando a proteção de timeout.

Diagnóstico proposto: força antes/depois dos ganhos, clipping por efeito/família e na soma, porcentagem de amostras cortadas, tempo contínuo saturado, slew, quantização, taxa efetiva, atrasos de loop/upload, renovações, efeitos ativos, blocos livres e relatórios rejeitados. Essas métricas representam comandos; sem sensor de torque, não são medição de torque mecânico.

Controle durante jogo: manter o conceito F7/F8 e acrescentar intensidade 0%; apresentar valor aplicado. Parada de emergência deve zerar, desarmar e exigir rearme separado, sem reaplicar estado antigo automaticamente. Prever controle local alternativo ao teclado, limitado ao usuário da sessão. Falha de backend, desconexão, perda de supervisão ou erro de protocolo que comprometa o estado desarmam; silêncio de comandos PID sozinho não invalida um efeito infinito legítimo.

## Plano incremental e critérios de aceite

| Etapa | Entrega pequena | Critério de aceite | Uso físico |
|---|---|---|---|
| **0 — esta entrega** | Auditoria, hashes e testes offline | Contrato v0.8 igual; lacunas reproduzidas e explícitas | Nenhum |
| 1 | Captura/replay e decoder normalizado, sem mudança na entrada | CSVs existentes reproduzidos; testes de sinal, unidades, direção e transportes; ampliar corpus quando houver captura de corrida | Backend nulo; coleta em sessão de jogo depende de autorização |
| 2 | Gerenciador de efeitos | Pool cheio/liberação, tempos/loops, Pause/Continue e Enable/Disable corretos | Nenhum |
| 3 | Renderer por famílias | Vetores de referência Constant/Spring/Damper/Periodic/Friction; mistura sem clipping antecipado; política explícita para Ramp/Inertia/Custom | Nenhum |
| 4 | Backend EVDEV e controles/diagnóstico com mock | Teto sempre respeitado após slew; intensidade zero; emergência travada; deduplicação com keepalive; erros e limpeza testados | Nenhum |
| 5 | Validação supervisionada gradual | Primeiro sinal/polaridade e stop; depois um efeito por vez; por fim replay de cenário e jogo, medindo clipping/latência | **Somente após aprovação** |
| 6, opcional | Avaliar condições nativas/userspace | Protocolo DFGT validado, exclusividade de escrita e restauração testadas; comparar com Constant | **Nova aprovação; sem adoção automática** |

Prioridade de implementação: corrigir o teto após slew e separar segurança de semântica, depois direção/sinal/escala, timeline e mistura. Nenhum aumento de boost deve ser usado como evidência de correção da tradução.

## Testes entregues e como executar

Arquivo: `tests/test_ffb_audit.py`, somente biblioteca padrão Python. Não importa os scripts de operação: extrai por AST apenas constantes, funções de dados e classes auditadas. Exclui `run`, binding e o entrypoint; não oferece `InputDevice`, `os` ou acesso a descritores de dispositivo às classes carregadas. O backend recebe somente um dispositivo falso.

```sh
python3 -B -m unittest discover -s tests -v
```

Resultado: **18 testes: 5 passaram e 13 divergências conhecidas reproduzidas como `expectedFailure`**. O resultado `OK (expected failures=13)` não significa FFB corrigido. Cada correção futura deve remover a marca correspondente e satisfazer o teste; um sucesso inesperado obriga a revisão da marca.

Os CSVs reais testam handshake, classificação do corpus e ausência de reprodução indevida. Os vetores sintéticos testam as divergências. Não foi executado `--self-test` dos bridges nem qualquer script de teste físico. Alterações desta entrega se limitam a documentação, manifesto e testes offline; os controles e o FFB de operação permanecem como estavam.
