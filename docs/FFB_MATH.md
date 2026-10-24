# Convenções matemáticas do núcleo offline

As referências v0.8/v0.13 e o descritor UHID permanecem congelados. Este documento descreve o pacote em `src/`, ainda sem conexão ao launcher ou ao hardware.

## Direção e eixo de saída

O eixo de direção é X; Y do descritor de entrada não é um segundo atuador. A coordenada matemática cresce com ABS_X, de 0 até 16383, com centro 8191,5. O sinal da força é uma convenção algébrica do núcleo, cuja correspondência mecânica ainda exige ensaio autorizado.

Para efeitos direcionais, a projeção é `p = sin(theta)`: 0°/180° dão zero em X, 90° dá +1, 270° dá −1. A amplitude signed é multiplicada por p; dois sinais negativos se cancelam. Este é o fator usado pelo caminho Constant em new-lg4ff. O pedido de saída conserva direção Linux `0x4000`; seu nível signed recebe o resultado algébrico. Não se infere a polaridade mecânica do DFGT apenas pelo rótulo left/right da API.

O descritor declara centésimos de grau e limite 35900: 9000 significa 90°. O decoder não interpreta esses bytes como a escala Linux 0..65535. `hid-pidff` reescala direção Linux para o limite lógico do descritor; isso pode introduzir uma pequena diferença angular antes de o pacote receber os bytes. Não se aplica uma segunda reescala adivinhando o host. Os pontos cardinais são exatos, eliminando resíduos de seno nos eixos ortogonais.

No byte de flags de Set Effect, bit 0 habilita X, bit 1 habilita Y e bit 2 habilita direção polar. Com direção habilitada, a projeção usa o ângulo; sem ela, X habilitado seleciona o eixo X diretamente. Y isolado não produz torque de direção. Até receber Set Effect, a compatibilidade do núcleo mantém o eixo X como padrão explícito.

Fontes consultadas: [new-lg4ff, direction_gain e calculate_constant](https://github.com/berarma/new-lg4ff/blob/master/hid-lg4ff.c), [Linux hid-pidff, set_effect_direction](https://github.com/torvalds/linux/blob/master/drivers/hid/usbhid/hid-pidff.c), [direção Linux em input.h](https://github.com/torvalds/linux/blob/master/include/uapi/linux/input.h), [Direction Enable no USB PID, seção 5.1.1](https://www.usb.org/sites/default/files/pid1_01.pdf). A direção de origem de um efeito e a direção do deslocamento produzido são conceitos diferentes na [documentação DirectInput](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee417536(v=vs.85)).

## Condições: campos e reação no eixo X

Os campos signed são normalizados por seus extremos reais: `n(s)=s/32767` se s≥0, `s/32768` se s<0. Isso preserva centro zero e os extremos −1/+1 sem overflow em −32768. Saturações e deadband usam `u/65535`, nunca a escala signed da força. Assim, saturação 16384 representa aproximadamente 25%, não 50%.

Defina p como a projeção do efeito, c como CP Offset normalizado, d como deadband e q como a métrica projetada. A lei fica:

```text
q = p * métrica
se q > c+d: e=q-c-d; C=Cpositivo; S=Spositivo
se q < c-d: e=q-c+d; C=Cnegativo; S=Snegativo
caso contrário: força=0
R = C*e                         Spring, Damper, Inertia
R = C*sign(e)                   Friction, modelo Coulomb
F_X = -p * clamp(R, -S, +S) * 32767
```

O sinal − expressa a reação restauradora/resistiva no sistema de coordenadas do núcleo: coeficiente positivo resiste ao deslocamento da métrica; coeficiente negativo inverte a reação. A expressão `A(q-q0)` da documentação PID é tratada como resposta da condição, separada da reação aplicada. A relação entre esse sinal algébrico e o sentido mecânico permanece uma hipótese de integração a validar, sem compensação por `abs()`, inversão por família ou comando físico nesta etapa.

Spring usa posição normalizada; Damper usa velocidade dividida por `velocity_reference=2` unidades de posição/s; Inertia usa aceleração dividida por `acceleration_reference=4` unidades de posição/s². Essas duas referências são **escolhas explícitas do sintetizador**, configuráveis e ainda não calibradas contra firmware. Saturação é aplicada por condição, antes do ganho do efeito. Friction usa a velocidade como métrica, mantém amplitude Coulomb fora do deadband e zera no ponto neutro. Não tenta simular atrito estático sem sensor de torque.

Com direção polar, projetar métrica e resposta é essencial: uma Spring simétrica passa a ter ganho proporcional a `sin(theta)²`. Inverter a direção em 180° não converte uma mola restauradora simétrica em repulsora. Em modo de eixos, o bloco de condição X não é rotacionado; o bloco de Y é diagnosticado e não sobrescreve X. CP Offset, coeficientes, saturações e deadband se aplicam à métrica de cada condição, inclusive às condições dinâmicas. Nenhum envelope é aplicado a condições.

O estimador de movimento foi separado da lei: usa timestamps, filtro de primeira ordem de 45 ms e reset das derivadas após lacuna maior que 250 ms. Timestamps repetidos ou separados por menos de 1 µs são agrupados. Essas políticas não são exigências PID; os testes das leis podem fornecer `MotionState` diretamente, enquanto testes independentes verificam as derivadas e limites do estimador. A futura integração deve fornecer amostras com timestamps de entrada, não confundir frequência do renderer com a do sensor.

O vetor antigo de inversão foi corrigido de −32767 para **−32768** quando o teste exige exatamente o mesmo módulo de +32767. O assert de inversão continua exato, e um teste adicional conserva o caso −32767 e verifica sua amplitude distinta em 1 LSB. Não se substituiu sinal por módulo nem se ampliou tolerância para mascarar o defeito.

Comparação: `new-lg4ff` e `lg4ff_userspace` preservam sinais nos comandos e usam `center ± deadband/2` ao codificar limites do firmware; isso não autoriza dividir por dois o deadband HID, cuja semântica é c±d. Seus caminhos de resistência não reproduzem todos os offsets/deadbands em software, e o primeiro aproxima Inertia como Damper. Este núcleo sintetiza as leis com uma convenção documentada, sem alegar equivalência física com esses caminhos. Fontes: [new-lg4ff](https://github.com/berarma/new-lg4ff/blob/master/hid-lg4ff.c), [lg4ff_userspace](https://github.com/Kethen/lg4ff_userspace/blob/main/force_feedback.c), [condições DirectInput](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee416335(v=vs.85)), [DICONDITION](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee416601(v=vs.85)).

## Envelope, tempo e forma de onda

Attack/Fade Level são amplitudes absolutas 0..32767. Para sustain M, ataque A e nível inicial L, a amplitude é `L+(M-L)*t/A`. No fade, é `M+(Lf-M)*(t-(D-F))/F`. A interpolação é calculada no domínio de milissegundos dos campos, evitando deslocamentos de arredondamento causados por subtrações de segundos em ponto flutuante. Valores de envelope fora dos limites declarados são rejeitados sem alterar o estado anterior.

Constant conserva o sinal de seu nível; o módulo é usado apenas para calcular a amplitude do envelope. Em Periodic, somente a magnitude oscilante recebe o envelope; offset é a baseline e permanece presente, sujeito depois ao ganho/direção. Attack pode superar a amplitude de sustain. Ganho por efeito multiplica o sinal já envelopado; ganho do dispositivo vem depois, sem modificar tempos ou CP Offset. Condições não usam envelope.

Start Delay precede a timeline de Attack. Duração zero não toca; infinito executa Attack e sustain sem fade automático; na duração finita o efeito cessa na fronteira D. Não foram corrigidos nesta etapa Loop Count, reinício por Start e Pause/Continue.

Quando A+F>D, a política do núcleo reduz A e F proporcionalmente para caber em D, mantendo continuidade e evitando multiplicar dois envelopes. É uma política explícita para sobreposição, não uma afirmação de paridade com o firmware. Ramp usa o intervalo entre Attack e Fade para ir do início ao fim; sem intervalo disponível, ambos se encontram num único valor interpolado para evitar salto. Ramp infinito mantém o início após Attack. O descritor unsigned de Ramp ainda não representa diretamente uma rampa que cruza de força negativa para positiva.

Phase e Direction usam centésimos de grau; a fase é avaliada módulo o período antes de somar a fase inicial. Os vetores de onda acompanham new-lg4ff: Sine cruza zero em fase zero; Square começa positivo; Triangle começa no pico positivo; Saw Up começa negativo e Saw Down positivo. A correção de Triangle evita um desvio de quarto de período presente no renderer anterior. A taxa de amostragem física e aliasing não são resolvidos pela matemática da forma de onda.

Comparações de implementação: [new-lg4ff, calculate_constant/ramp/periodic](https://github.com/berarma/new-lg4ff/blob/master/hid-lg4ff.c) e [lg4ff_userspace, mesmas funções](https://github.com/Kethen/lg4ff_userspace/blob/main/force_feedback.c). O segundo também mantém offset separado da magnitude envelopada; essas referências ajudam a verificar o cálculo, sem serem usadas como oráculo de todas as políticas de sobreposição.

Os níveis absolutos e o ataque acima do sustain também são documentados em [DIENVELOPE](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee416625(v=vs.85)) e [Basic Concepts of Force Feedback](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee416225(v=vs.85)).

## Mistura simultânea e clipping

`components()` retorna componentes de amplitude ampla, sem arredondamento ou clipping por família. Cada efeito é calculado com sua direção, envelope ou saturação condicional; em seguida recebe ganho do efeito. As contribuições são somadas com `math.fsum`, depois multiplicadas pelo ganho do dispositivo. Essa soma preserva resíduos pequenos e cancelamentos mesmo entre forças grandes. `raw_level()` é apenas um helper legado com teto/quantização; não deve alimentar o backend novo.

No mixer linear, para componentes B e ganho de saída G:

```text
T = fsum(B)
pedido = G*T
limitado = clamp(pedido, -teto, +teto)
```

O ganho do efeito é byte/100, ganho do dispositivo byte/255 e ganho G da política de saída é `boost*teto/32767`, preservando a semântica da intensidade existente. Um ajuste periódico opcional é aplicado antes da soma; o padrão é 1.0. A quantização inteira ocorre na saída; slew vem depois, seguido novamente pelo teto para preservar as correções anteriores. A saturação de cada condição faz parte do protocolo; não é um corte arbitrário por família.

Foram removidos do núcleo novo os antigos `spring_share`/`damper_share` e os tetos implícitos de Friction/Inertia. Não houve mudança dessas opções nas referências históricas. Dois Constant +32767 e uma Spring −32767 resultam em +32767; dois componentes de 0,4 resultam em 1 após quantização, sem arredondamento antecipado para zero.

Uma saída finita não pode reproduzir fielmente uma mola acima do teto e também uma vibração sobreposta. Para reservar espaço de forma explícita, o mixer oferece `condition_headroom=h`, **desativado por padrão**. Primeiro soma todas as condições, preservando cancelamentos nesse agregado; então limita esse fundo a `(1-h)*teto`, em unidades de saída já com ganho. Soma os efeitos direcionais/transientes e aplica o teto final. Com h=0,25, uma mola intensa deixa até 25% da faixa para cada sinal do transiente, sem clipping final se o transiente estiver dentro dessa reserva. Essa opção altera deliberadamente a amplitude das condições e não é anunciada como tradução fiel do PID.

`MixResult` separa pedido sem reserva, pedido após reserva, saída limitada, clipping final e redução do agregado condicional. `ConstantOutput.last_mix`, `saturation_ticks` e `condition_limited_ticks` expõem essas diferenças. Números não finitos, overflow e famílias desconhecidas são rejeitados; se uma saída mock já estiver ativa, a política para e desarma antes de propagar o erro. Esses diagnósticos medem comandos calculados, não torque mecânico.

Vetores de seis efeitos simultâneos validam Spring, Damper, Inertia, Friction, Constant e Sine até o backend mock. Outros vetores cobrem ganho combinado, inversões, extrema saturação, reserva opcional e cancelamento de valores grandes. O corpus real permanece restrito aos 132 registros de inicialização; nenhum teste sintético é apresentado como captura do DiRT 5.
