# Intensidade física: auditoria, faixa completa e diagnóstico

## Resultado

O operador relatou força praticamente imperceptível nos ensaios de 5% e 10%. A faixa física agora aceita **0 a 100%** na inicialização e durante a execução, inclusive máximo zero. Os padrões continuam 10% inicial e máximo 20%. A intensidade inicial não pode exceder o máximo configurado. F7/F8 ajustam 5 pontos percentuais, limitados a zero e ao máximo escolhido. O máximo não é multiplicado novamente como ganho.

Não foi acrescentado boost. `--ffb-strength 100 --ffb-max-strength 100` aplica ganho de usuário 1,0 e permite nível Constant até 32767. O backend aceita o intervalo signed nativo −32768..32767; o mixer existente mantém seu teto simétrico −32767..32767. Essa diferença de um LSB no extremo negativo não reduz a escala positiva nem muda o nível mínimo de oito bits no caminho Logitech auditado.

375 testes passam, sem falhas esperadas ou ignorados. O teste integrado percorre relatórios HID PID, renderer, supervisor e ABI EVDEV simulada: envia 32767 com direção `0x4000` e `FF_GAIN=65535`, embora o mock principal continue em 20%. Todos os testes bloquearam aberturas de dispositivos reais. Nenhum motor foi acionado pelo agente, nenhum driver foi carregado/instalado ou alterado e nenhum push foi feito.

## Onde a intensidade diminui

| Etapa | Comportamento auditado |
|---|---|
| HID PID | Constant é signed s16; ganho por efeito usa a faixa 0..100 do descritor e Device Gain usa 0..255. Não há nova divisão de força inteira por 32767 no backend. |
| Renderer | Aplica ganho do efeito, projeção e ganho PID. Componentes permanecem somas amplas; podem ultrapassar a escala de um efeito. Ganhos solicitados pelo jogo continuam sendo respeitados. |
| Mock principal | O padrão de 20% afeta somente a simulação. `PhysicalClient.publish()` envia os componentes anteriores a esse ganho/slew, não `final_force`. O teste integrado comprova essa separação. |
| Mixer físico | Aplica uma vez `g = round(32767 × intensidade / 100) / 32767`, depois limita a `±round(32767 × intensidade / 100)`. Boost e ganho periódico são 1; reserva condicional é zero. |
| Máximo configurado | Restringe a posição do controle de intensidade; não constitui outro multiplicador. A antiga restrição de 30% no supervisor e a de 70% na política compartilhada foram removidas. |
| Slew | Estava fixo em 16383,5 unidades/s: aproximadamente 2 s de zero ao máximo e 4 s para inverter os extremos. Atenua oscilações rápidas, mesmo quando o teto permite força maior. Agora é explícito/configurável por `--ffb-slew-rate`. O padrão conservador permanece, mas não há teto de 32767 unidades/s para essa configuração. |
| Taxa de saída | Atualizações normais continuam em até 45 Hz. Zero/reduções/paradas têm prioridade. Não há recuperação de comandos antigos em rajadas. Isso limita resolução temporal, não a amplitude estável permitida. |
| Supervisor | Só cálculos novos e válidos autorizam saída. Watchdog de 100 ms, perda do jogo, emergência e prazo da sessão podem zerar a força. Não dependem de novos relatórios PID para manter um efeito infinito legítimo. |
| EVDEV | Um slot FF_CONSTANT reutilizado, nível signed sem conversão adicional, direção fixa `0x4000`. FF_GAIN recebe 65535 ao iniciar reprodução; stop/cleanup usam ganho zero. Não aplica o percentual do usuário outra vez em FF_GAIN. |
| Driver Logitech | O caminho instalado reduz a resolução antes de montar o comando de oito bits. Não foi encontrado um teto percentual de 30%/90% nesse caminho. Isso não mede torque nem exclui questões de firmware, alimentação ou outro escritor. |

A intensidade é um **ganho proporcional e um teto**, não apenas um teto. Por exemplo: renderer=1000, intensidade=10% → aproximadamente 100 no EVDEV; renderer=32767 → 3277. Em 5%, um sinal do jogo de 10% da escala vira aproximadamente 0,5% antes do slew/quantização do driver. Essa composição explica comandos muito pequenos sem haver aplicação duplicada do ganho de usuário.

### Evidência nos ensaios reais

Capturas e traces foram preservados e verificados em replay pareado com `--verify-compatible`, comparando respostas PID, estados, forças e ações do backend mock:

| Trace de origem | Intensidade / máximo | Frames físicos | Pico EVDEV | Tempo entre primeira/última saída não zero |
|---|---|---:|---:|---:|
| `dirt5-policy-20261024-211132.jsonl.gz` | 5% / 10% | 11992 | 1621 (4,95% da escala) | 29,999 s |
| `dirt5-policy-20261024-223235.jsonl.gz` | 10% / 15% | 10116 | 3147 (9,60% da escala) | 29,999 s |

Ambos tinham **`session_s=30`**. O prazo foi atingido em 84,112 s e 75,468 s de trace, respectivamente; a captura continuou por 134,529 s e 105,655 s. Depois do prazo, o supervisor registrou `supervised_session_deadline` e zerou a força. O prazo começa na primeira saída ativa, que pode ocorrer no menu. Não é falha de ganho: é uma parada configurada que explica trechos posteriores sem força.

Os ganhos PID registrados eram 1,0. Os picos do renderer antes da política física foram 60355,887 e 37000,650, pois são somas de efeitos, não um único s16. Os traces antigos não registravam o ganho realmente escrito no EVDEV: portanto, não provam seu valor físico nem permitem descartar interferência externa. A escrita de 65535 foi comprovada no código e nos testes ABI falsos; o novo trace registra esse comando.

Reprocessando **os mesmos eventos e tempos**, com força constante escolhida de 100%, mesmas proteções e backend mock:

| Origem | Pico com slew 16383,5/s | Pico com slew 327670/s |
|---|---:|---:|
| Ensaio de 5% | 16599 | 32426 |
| Ensaio de 10% | 26172 | **32767**, em 12 frames |

Não são novos testes de motor nem comparação de torque. O começo do prazo continua condicionado à primeira saída ativa de cada simulação. A comparação mostra que liberar somente o percentual não elimina a atenuação temporal do slew. Fontes, configurações, hashes e resultados estão em [FFB_INTENSITY_AUDIT.json](FFB_INTENSITY_AUDIT.json).

### Driver instalado

Kernel inspecionado: `7.2.7-valve2-2-neptune-72-g31504582a073`. Os módulos `hid-logitech.ko.zst` e `ff-memless.ko.zst` foram apenas lidos e descomprimidos em `/tmp`; não foram carregados, substituídos nem versionados.

A desmontagem de `ml_play_effects` contém o deslocamento aritmético de oito bits (`sar $0x8,%dx`, endereço relativo `0xa2a`) e limites −128..127. `lg4ff_play` soma 128 e limita o comando a 0..255; zero desativa a força. A resolução é da ordem de 256 unidades EVDEV por passo, com truncamento assimétrico. Esses achados concordam com as implementações de referência [ff-memless](https://github.com/torvalds/linux/blob/master/drivers/input/ff-memless.c) e [hid-lg4ff](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-lg4ff.c).

A direção `0x4000` projeta o Constant no eixo X; inverter o nível signed inverte o comando. A polaridade mecânica continua selecionável por `--ffb-polarity -1`, sem modificar os coeficientes PID. A faixa de ganho EVDEV e a separação upload/play estão descritas na [documentação FF do kernel](https://docs.kernel.org/input/ff.html).

## F10 e physical-trace

`force_pipeline` aparece no F10, no status do supervisor e em cada resultado do trace:

- `renderer_total` no resultado principal: soma calculada antes do ganho de usuário, sem clipping. `renderer_signed_reference` mostra a referência limitada à escala −32768..32767 sem substituir a soma original.
- `game_force.pid_gain` e `effects`: ganho PID e ganhos dos efeitos recebidos do jogo.
- `user_strength_percent`, `maximum_percent`, `user_gain`: intensidade escolhida, máximo e ganho efetivo após quantização do teto.
- `after_user_gain`, `after_clipping`, `after_safety_slew`, `evdev_magnitude`: força em cada fronteira. `evdev_level` conserva o sinal.
- `slew_per_s`, `stage`: configuração e decisão mais recente (`direct`, `slew`, `rate_gate`, `safety_stop` etc.). Alterar intensidade não reaplica amostra antiga: `strength_change_awaiting_frame` identifica a espera pelo cálculo novo, mantendo reduções de teto imediatas.
- `uploads_last_second`, `uploads_per_second`, `plays_per_second`: operações reais solicitadas pela política em janela de até um segundo. São diferentes da frequência de comandos PID ou de frames calculados. Histórico diagnóstico é limitado a 256 timestamps por contador; não é fila de forças.
- `loss_percent`: perdas relativas de magnitude por ganho de usuário, clipping e etapa temporal/segurança; `total` compara magnitude enviada com soma original. Não somar percentuais de etapas sucessivas. Sem referência não zero, a perda é `null`.
- `tracking_error_percent` e `sign_mismatch`: distinguem magnitude semelhante com sentido ainda oposto durante inversão limitada por slew/rate gate. Percentual sem representação finita é `null`.

O bloco `io` registra **`gain_commanded`**, **`level_uploaded`**, **`level_commanded`**, uploads, deduplicações e latências EVDEV. São comandos aceitos pelo sistema, não leitura de torque ou confirmação de estado interno do firmware. `evdev_gain_expected` na política indica seu estado de reprodução esperado; comparar com `io.gain_commanded` para conferir o que o backend escreveu.

FF_GAIN/autocenter são globais ao dispositivo e não oferecem readback confiável nesta API. Outro escritor pode alterá-los; o flock do bridge é cooperativo. O modo físico continua exclusivo ao VID/PID DFGT e o passivo não instancia esse backend. O modo passivo não envia forças, mesmo que receba parâmetros físicos de 100% sem `--enable-ffb`.

Traces novos registram `diagnostics_version=2` e o replay exige a equivalência dos diagnósticos. Para traces v1, somente os campos adicionados são opcionais: todos os campos originais e decisões continuam conferidos. Não se deduz telemetria antiga ausente como se tivesse sido capturada.

## Comando preparado para 100%

Iniciar o runner; esperar `[PRONTO]`, então abrir o DiRT 5 normalmente com o G29 virtual selecionado. Não executar junto de outros bridges/controladores de FFB. Os launchers históricos continuam intactos.

```sh
cd /home/deck/dfgt-bridge
sudo /usr/bin/python3 -B scripts/run-passive.py \
  --identity g29 --auto-pidff --gas-axis y --enable-ffb \
  --ffb-strength 100 --ffb-max-strength 100 \
  --ffb-slew-rate 327670 --ffb-seconds 120 \
  --capture "captures/dirt5-100-$(date +%Y%m%d-%H%M%S).jsonl.gz"
```

O slew explícito permite aproximadamente 0,1 s de zero à escala máxima e 0,2 s entre extremos, sem boost. Zero e emergência não aguardam essa rampa. A sessão para 120 s depois da primeira saída ativa, incluindo menus e pausas nesse prazo; `--ffb-seconds` continua configurável em 30..240 s. F7/F8 percorrem 0..100% neste comando. F9/`ffb-stop` desligam e exigem `ffb-arm` explícito e cálculo novo; não alternam automaticamente.

O physical-trace é criado automaticamente com o nome completo da captura acrescido de `.ffb.jsonl.gz`. Replay, sem dispositivos:

```sh
python3 -B scripts/replay-capture.py captures/NOME.jsonl.gz \
  --physical-trace captures/NOME.jsonl.gz.ffb.jsonl.gz
```

A faixa completa é autorização de configuração, não garantia de torque mecânico. Confirmar o novo nível/ganho no F10 e trace antes de atribuir força ausente ao renderer ou adotar alterações de driver. A fidelidade de mola, impactos e retorno ao centro permanece para a etapa seguinte.
