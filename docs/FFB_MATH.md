# Convenções matemáticas do núcleo offline

As referências v0.8/v0.13 e o descritor UHID permanecem congelados. Este documento descreve o pacote em `src/`, ainda sem conexão ao launcher ou ao hardware.

## Direção e eixo de saída

O eixo de direção é X; Y do descritor de entrada não é um segundo atuador. A coordenada matemática cresce com ABS_X, de 0 até 16383, com centro 8191,5. O sinal da força é uma convenção algébrica do núcleo, cuja correspondência mecânica ainda exige ensaio autorizado.

Para efeitos direcionais, a projeção é `p = sin(theta)`: 0°/180° dão zero em X, 90° dá +1, 270° dá −1. A amplitude signed é multiplicada por p; dois sinais negativos se cancelam. Este é o fator usado pelo caminho Constant em new-lg4ff. O pedido de saída conserva direção Linux `0x4000`; seu nível signed recebe o resultado algébrico. Não se infere a polaridade mecânica do DFGT apenas pelo rótulo left/right da API.

O descritor declara centésimos de grau e limite 35900: 9000 significa 90°. O decoder não interpreta esses bytes como a escala Linux 0..65535. `hid-pidff` reescala direção Linux para o limite lógico do descritor; isso pode introduzir uma pequena diferença angular antes de o pacote receber os bytes. Não se aplica uma segunda reescala adivinhando o host. Os pontos cardinais são exatos, eliminando resíduos de seno nos eixos ortogonais.

No byte de flags de Set Effect, bit 0 habilita X, bit 1 habilita Y e bit 2 habilita direção polar. Com direção habilitada, a projeção usa o ângulo; sem ela, X habilitado seleciona o eixo X diretamente. Y isolado não produz torque de direção. Até receber Set Effect, a compatibilidade do núcleo mantém o eixo X como padrão explícito.

Fontes consultadas: [new-lg4ff, direction_gain e calculate_constant](https://github.com/berarma/new-lg4ff/blob/master/hid-lg4ff.c), [Linux hid-pidff, set_effect_direction](https://github.com/torvalds/linux/blob/master/drivers/hid/usbhid/hid-pidff.c), [direção Linux em input.h](https://github.com/torvalds/linux/blob/master/include/uapi/linux/input.h), [Direction Enable no USB PID, seção 5.1.1](https://www.usb.org/sites/default/files/pid1_01.pdf). A direção de origem de um efeito e a direção do deslocamento produzido são conceitos diferentes na [documentação DirectInput](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee417536(v=vs.85)).
