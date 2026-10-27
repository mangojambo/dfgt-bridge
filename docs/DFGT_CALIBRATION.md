# DFGT: retorno ao centro, calibração e transições

Para comparar durante a corrida, **F5 seleciona ×3 e F6 seleciona ×4**, com
interpolação temporária de 300 ms, sem salvar o perfil ou reiniciar o G29.
F10 mostra os ganhos, forças e estatísticas de comando. Ver
[Spring em tempo real](LIVE_SPRING.md) para operação, segurança e limitações.

Esta documentação acompanha a candidata v1.1.0 em `fix/ffb-core`. O operador
confirmou manualmente no SteamOS/DiRT 5 a melhora de retorno ao centro em ×2 e
×3, o funcionamento de ×4 com diferença pequena ante ×3, os atalhos F5/F6 e
o slider salvo em ×3. São observações de uma instalação, não medições de
torque nem certificação de compatibilidade universal.

## Causa comprovada da perda de FFB

O novo log do operador registra watchdog com idade **105196896 ns** após
`5a010300` (Stop do bloco 1), saída já em zero/sem play, jogo ativo, sem hard
fault e sem emergência. A política anterior tornava esse intervalo em repouso
um latch permanente. Isso está reproduzido com esse mesmo intervalo offline.
O log não identifica a origem temporal do atraso ou quantos Stops foram enviados.

A [correção das transições](FFB_TRANSIENT_WATCHDOG.md) mantém zero em repouso
até chegar geração nova, época atual e cálculo posterior ao timeout, com
idade menor que 100 ms. Não reproduz o último efeito EVDEV nem renova força
em `poll`. Com força ainda ativa, o watchdog continua zerando e travando.
F9, teclado perdido, emergência, saída do jogo e falhas graves exigem rearme
humano. Repetições idempotentes de Stop não avançam a barreira continuamente;
a primeira parada e mudanças efetivas continuam invalidando os frames antigos.

## Investigação da força fraca

O F10 fornecido tem ganho global 100%, FF_GAIN comandado 65535 e Spring em
cerca de −2521, correspondente a cerca de 7,7% da faixa de comando. Não existe
no caminho atual um teto escondido de Spring em 7,7%: o valor vem do renderer,
antes da saída física. Sem posição e Set Condition/Set Effect desse instante,
não se consegue atribuir essa amplitude a um coeficiente específico ou
comprovar que a escala HID esteja errada. Esse valor não é medição de torque
nem do pico da corrida.

Revisão da tradução atual, não da v0.13 histórica:

| Etapa | Resultado da revisão |
|---|---|
| Posição | Curso normalizado −1..+1 do centro ao extremo; Spring usa a posição, não a derivada. |
| Centro/coeficientes | s16: negativo /32768, positivo /32767. Não se usa módulo do coeficiente. |
| Saturação/deadband | u16 /65535; condição inativa entre centro −deadband e centro +deadband. |
| Sinal | Coeficiente positivo gera reação ao deslocamento; em X, `F * (posição-centro) < 0` fora do deadband. Ganhos não negativos conservam o sinal individual. |
| Ganhos PID | Ganho do efeito /100 e do dispositivo /255 aplicados uma vez no renderer. |
| Mistura | Soma ampla das famílias antes de clipping. Cancelamentos Spring/Constant são algébricos; não há retificação ou clamp por família no mixer normal. |
| Saída | Polaridade aprovada −1, ganho 100%/máximo 100%, slew 327670 e teto final ±32767, dentro da ABI EVDEV −32768..32767. |

As convenções e aproximações completas estão em [FFB_MATH.md](FFB_MATH.md).
As conversões de campos são coerentes com [Linux hid-pidff](https://github.com/torvalds/linux/blob/master/drivers/hid/usbhid/hid-pidff.c)
e a métrica de Spring/deadband com [USB PID 1.0, seção 5.3](https://www.usb.org/sites/default/files/documents/pid1_01.pdf).
Essas fontes não medem a resposta de um G920 nem provam a versão exata do
kernel/Proton da sessão do operador.

Damper mantém referência de velocidade 2 unidades normalizadas/s; Inertia,
aceleração 4 unidades/s². O filtro de movimento de 45 ms e essas referências
são escolhas do sintetizador, sem calibração de torque G920. Friction usa
modelo Coulomb, sem medição de atrito estático. Periodic permanece limitado
pela amostragem de saída de até 45 Hz; Ramp mantém a limitação do descritor
unsigned. Aumentar Spring não resolve essas limitações por si só.

`FF_AUTOCENTER` nativo permanece em zero. Ele é um recurso separado de Spring
do jogo; ligá-lo permanentemente acrescentaria força que poderia persistir em
menus/Disable e somar-se à condição já renderizada. O retorno nesta candidata
vem exclusivamente de efeitos habilitados e ativos do jogo. O caminho de
Constant do [hid-lg4ff](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-lg4ff.c)
recebe um agregado de resolução reduzida do [ff-memless](https://github.com/torvalds/linux/blob/master/drivers/input/ff-memless.c):
comandos muito pequenos podem perder resolução. Isso não autoriza inventar
uma força mínima ou presumir equivalência de torque entre os motores.
O bridge não detecta menus automaticamente: se o jogo mantiver um efeito
ativo no menu, ele continua sendo um efeito do jogo. A candidata não adiciona
uma mola independente desse estado.

## Calibração implementada

O estágio físico agora aceita multiplicadores independentes para **Spring,
Damper, Friction, Inertia, Constant, Ramp e Periodic**. São aplicados depois
dos ganhos/saturação PID e antes da soma e do teto final. Não alteram as
unidades HID recebidas. Coeficientes e deadband permanecem intactos no renderer;
a saturação da condição também recebe o multiplicador de calibração na saída.

```text
componentes_calibrados[família] = componentes_PID[família] × multiplicador[família]
pedido = soma(componentes_calibrados) × polaridade × ganho_global
alvo = clamp(pedido, -teto_global, +teto_global)
saída = slew(alvo), novamente dentro do teto_global
```

Multiplicadores aceitos: números finitos 0..8; negativos, bool, NaN/Inf,
famílias desconhecidas e overflow são rejeitados. A faixa 0..8 é de
configuração, não uma afirmação de capacidade do motor. Nenhum multiplicador
permite exceder o teto de saída, ignorar Stop/Disable ou rearmar F9.

**Padrão oficial v1.1.0: Spring ×3, demais famílias ×1.** É uma
calibração de resposta, não uma correção arbitrária da interpretação HID,
nem um fator de torque fisicamente medido. `ffb_spring_gain=3.0` é centralizado
nos defaults do projeto e aplicado automaticamente após reinstalar. Os defaults
aprovados globais não foram reduzidos.

CLI: `--ffb-spring-gain`, `--ffb-damper-gain`, `--ffb-friction-gain`,
`--ffb-inertia-gain`, `--ffb-constant-gain`, `--ffb-ramp-gain`,
`--ffb-periodic-gain`. Sem argumentos extras, Spring usa 3 e as demais famílias
usam 1. O campo principal de Spring é `"ffb_spring_gain": 3.0`; o mapa
`ffb_effect_gains` conserva as personalizações das outras famílias. São opções
de personalização, não requisitos para usar Spring ×3 na Steam.

O ProfileStore compartilhado faz a migração tanto na leitura pelo launcher
quanto na instalação, que salva o resultado no perfil existente:

| Perfil recebido | Resultado |
|---|---|
| Sem campo de Spring, inclusive perfil anterior à calibração | Spring ×3 |
| Mapa antigo vazio ou somente outras famílias | Spring ×3, outras personalizações preservadas |
| `ffb_effect_gains.spring` explícito, inclusive 0 ou 1 | Convertido para `ffb_spring_gain` com o mesmo valor |
| `ffb_spring_gain` explícito | Preservado, inclusive valores diferentes de 3 |
| Ambos os formatos com valores conflitantes | Erro antes de modificar a instalação; sem sobrescrita silenciosa |

A migração é idempotente. GUI, CLI, launcher e serviço usam a mesma validação;
a GUI preserva o campo ao editar/salvar/preset sem mudanças de layout. O
launcher e o serviço aceitam ganhos personalizados válidos 0..8 e continuam
exigindo os demais parâmetros físicos aprovados. O serviço transmite o perfil
por arquivo privado ao runner, sem novos argumentos nas Launch Options.

F10 separa `renderer_components`/`renderer_total` originais de
`effect_calibration` (ganhos, componentes e soma após calibração). O pipeline
passa a calcular o ganho global/clipping a partir dessa soma. A barreira de
zero do produtor usa a mesma soma calibrada: cancelamentos novos não podem
ser perdidos por coalescing. Trace opcional v8 reexecuta a calibração e verifica
as decisões de recuperação; omitir/adulterar esses diagnósticos provoca erro.
Traces v1..v5 sem ganho explícito conservam o default histórico ×1 no replay;
a mudança de default não amplifica retroativamente as capturas antigas.

## Comparação quantitativa reproduzível

Gerar a tabela sem dispositivo, sem importar entrypoints de referências:

```sh
python3 -B scripts/dfgt-calibration-table.py
python3 -B scripts/dfgt-calibration-table.py --coefficient 8192
python3 -B scripts/dfgt-calibration-table.py --deadband 6554 --saturation 16384
```

A [tabela completa, nos dois sentidos](DFGT_SPRING_TABLE.md) passa pelo decoder
PID real e pelo supervisor com MockBackend, ganho 100%, polaridade −1 e slew
327670. Os resultados foram estabilizados por frames novos a cada 25 ms, sem
remover o slew. Esta tabela é a comparação offline histórica ×1/×2; a versão
v1.1.0 usa ×3 como padrão:

| Posição absoluta | EVDEV antes (módulo) | EVDEV depois (módulo) | Faixa depois | Clipping final |
|---:|---:|---:|---:|:---:|
| 10% | 3277 | 6553 | 20% | Não |
| 25% | 8192 | 16384 | 50% | Não |
| 50% | 16384 | 32767 | 100% | Não: chega exatamente ao teto |
| 100% | 32767 | 32767 | 100% | Sim: pedido ×2 excede o teto |

Há aumento real do **comando** em 10%, 25% e 50%; no extremo, o motor já
recebia o comando máximo e não ganha força adicional. Saturar mais cedo
também reduz a margem para detalhes sobrepostos; não é melhoria gratuita.
Com coeficiente 8192 (~25%), os módulos em 10/25/50/100% passam de
819/2048/4096/8192 para 1638/4096/8192/16384, sem clipping final.

Usando os componentes estáticos do F10 anterior, Spring −2521,08 e Constant
−1: a soma original é −2522,08; com Spring ×3 passa a −7564,24 e o alvo
EVDEV é +7564, aproximadamente 23,1% da faixa. Não se multiplica o número
EVDEV antigo 2535, pois ele pode representar outro instante do gate/slew.
Esse vetor não substitui uma captura temporal de corrida.

As duas direções foram verificadas. Para posição positiva, a força Spring
do renderer é negativa; para negativa, é positiva. A polaridade global −1
inverte o **comando EVDEV**, como já aprovado. O operador confirmou retorno ao centro com os ganhos Spring em uso. Os testes
offline desta documentação verificam direção matemática do comando, não medem
torque mecânico. Ganhos independentes podem mudar o sinal da soma quando famílias
se opõem: isso é consequência explícita da nova relação, não inversão oculta
de uma família ou eliminação artificial de cancelamentos.

## Uso normal no SteamOS

Com o DiRT 5 fechado, atualizar o checkout de desenvolvimento e executar:

```sh
sudo ./install.sh
```

Depois, abrir o DiRT 5 normalmente pela Steam. As Launch Options já instaladas
continuam iguais. Spring ×3 fica ativo sem editar JSON, configurar perfil ou
passar argumentos extras, exceto quando o perfil já contiver um ganho Spring
personalizado explícito, que será respeitado. A reinstalação atualiza o snapshot
do serviço; mudar somente o checkout não substitui o runtime instalado.

O slider da GUI permite salvar outro ganho entre ×1 e ×4 em incrementos de
×0,25 para sessões futuras. F5/F6 continuam temporariamente em ×3/×4.

## Observações físicas reportadas pelo operador

O operador informa que Spring ×2 melhorou o retorno ao centro, ×3 melhorou
mais, e ×4 funcionou com pouca diferença perceptível em relação a ×3. F5/F6
foram usados durante o jogo e o slider da GUI foi salvo em ×3. Isso confirma
esses comportamentos na instalação pessoal do operador; não determina torque,
não prova equivalência com G920 e não certifica outros jogos ou hardware.

Dados de saturação reportados numa sessão anterior: ×2 teve 4/1035 comandos
no teto (0,39%); ×3 teve 591/7229 (8,18%), e nos últimos cinco segundos
16/146 (10,96%). Pico EVDEV 32767, FF_GAIN comandado 65535, sem latch durante
a corrida. EVDEV é telemetria de comando, não uma leitura de torque. ×3 é o
padrão desta versão por escolha do operador; ×4 continua disponível para
comparação temporária. O teto final e as proteções permanecem ativos.

## Validação da candidata v1.1.0

`python3 -B -m unittest discover -s tests -v`: **574 testes, 573 passaram e um
smoke Tk ignorado por falta de display**, zero falhas esperadas. A mesma suite
passou com ResourceWarning como erro e audit hook impedindo dispositivos,
escritas de bind/unbind/new_id e subprocessos reais: zero tentativas.

Os novos vetores são rotulados sintéticos, com relógio determinístico e
MockBackend. Cobrem posições ±10/25/50/100%, centro, sinal dos coeficientes,
direção polar, saturação/deadband, sete ganhos individuais, cancelamentos,
clipping, Stop/Start/Disable/Pause/Free/Reset, lacunas superiores a 100 ms,
F9, perfil compartilhado, migração automática/reinstalação, personalizações
explícitas, launcher/serviço/runner com ×3 sem argumentos extras, IPC/coalescing
e replay v5/v6/v7/v8 sem amplificar retroativamente traces antigos.
Também foi usado um vetor estático dos componentes F10 do operador, sem
atribuir a ele validação de toda uma corrida. Não foram alterados descritor,
controles, referências v0.8/v0.13, fixtures reais, backend físico, GUI, versões
de release, drivers ou configuração instalada do SteamOS.
