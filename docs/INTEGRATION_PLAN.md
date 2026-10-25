# Plano de conexão à baseline v0.8

**Etapa mais recente:** integração automática Steam/systemd implementada, com 459 testes offline passando. Perfil físico aprovado preservado; instalação e validação Desktop/Gaming Mode ainda dependem do operador. Ver [STEAM_INTEGRATION.md](STEAM_INTEGRATION.md). Os estados abaixo são históricos.

**Estado atual (2026-10-25):** defaults aprovados 100%/100%, −1, slew 327670 e duração ilimitada, com F9/watchdog preservados. GUI de botões/eixos e perfis compartilhados com CLI implementados; 415 testes offline passam. Validação visual no SteamOS, sentido real do dial e navegação no jogo permanecem manuais. Ver [PROFILES_AND_MAPPING.md](PROFILES_AND_MAPPING.md). As etapas seguintes abaixo são registros históricos.

Estado mais recente: o operador ensaiou 5% e 10%. A faixa física agora é 0..100%, com slew configurável e diagnóstico de perdas; 375 testes offline passam. A prioridade é conferir intensidade, FF_GAIN e magnitude EVDEV antes de avaliar fidelidade. Auditoria e próximo comando: [FFB_INTENSITY.md](FFB_INTENSITY.md).

A regressão de enumeração foi corrigida: a associação automática PIDFF omitida na refatoração foi restaurada com propriedade do nó e prontidão driver/EVDEV/FF. A saída física só inicia depois dessa validação. São 346 testes offline passando; nenhum bind real ou motor foi executado. A próxima tarefa é conferir reconhecimento no SteamOS em modo passivo. Consulte [PID_BINDING.md](PID_BINDING.md), inclusive a limitação global de `new_id`.

## Estado após a implementação EVDEV supervisionada

O operador confirmou o acelerador em Y. A captura real corrigida e as duas anteriores passaram em replay completo. O backend EVDEV e a supervisão física foram implementados por opt-in, sem iniciar motor ou alterar drivers. O runner permanece passivo por padrão; `--enable-ffb` cria o processo separado de saída e teclas. Detalhes, limitações de exclusividade/temporização e próximo ensaio manual estão em [SUPERVISED_FFB.md](SUPERVISED_FFB.md). A descrição das etapas anteriores abaixo é histórica; a validação física continua pendente.

## Estado após a implementação passiva

A fachada e o runner passivo deste plano foram implementados em arquivos novos, com testes offline e sem execução em dispositivos. `scripts/run-passive.py` usa a entrada readonly e somente MockBackend; `scripts/replay-capture.py` confere capturas em memória. As referências e launchers continuam intactos. Consulte [PASSIVE_RUNNER.md](PASSIVE_RUNNER.md) para os comandos preparados, schema de captura, limites e critérios pendentes de validação real.

Duas capturas reais posteriores, fornecidas pelo operador, já foram conferidas offline. Elas revelaram a diferença de configuração Z/Y do acelerador; o CLI foi corrigido para Y. A próxima coleta passiva deve confirmar o pedal no jogo com esse mapeamento. Veja [CAPTURE_COMPARISON_20261024.md](CAPTURE_COMPARISON_20261024.md). A associação observada a `hid-universal-pidff` e os efeitos calculados não autorizam ainda conexão ao motor.

As decisões de CLOSE/OPEN, GET Input, estado PID assíncrono e duração ilimitada exclusivamente mock estão detalhadas em [PID_TRANSPORT.md](PID_TRANSPORT.md). A próxima fase é a coleta passiva real pelo operador, ainda sem backend físico. O plano original abaixo registra a sequência e suas restrições; a integração ao motor permanece não executada.

## Plano original da etapa anterior

O núcleo, o transporte em bytes e a saída simulada estão testados offline. As referências e launchers permanecem congelados. Este plano não adiciona um entrypoint real, não abre dispositivos e não associa drivers.

## Fronteiras propostas

```text
entrada v0.8 → report(state) → UHID Input 0x01, bytes idênticos
                         ↘ amostras com timestamp → MotionState
UHID eventos → PIDTransport → estado/pool/timeline → renderer/mixer
respostas ← tupla de bytes          ↓
                              MockBackend inicialmente
```

1. **Extrair uma fachada de entrada em arquivo novo**, sem editar as referências: `descriptor()`, `report()` e empacotamento UHID Input da v0.8. Os testes atuais de comparação AST/bytes devem passar também para essa fachada, incluindo pedais, hat, todos os botões, opções e limites de direção. A identidade G29 e a classificação Automobile continuam iguais. Nenhuma mudança de FFB deve deslocar bits de entrada.
2. **Criar uma camada de I/O separada**, mediante autorização para os testes que a utilizem. Ela faz uma leitura por evento e uma escrita completa por resposta retornada por `PIDTransport.handle()`. Usa a ABI UHID nativa e conserva o Report ID no payload. Não chama em paralelo o `respond_to_kernel()` histórico, evitando dupla aplicação/duplo ACK. Leituras/escritas parciais, desconexão e erros devem levar a cleanup, sem reaplicar comandos que já tiveram efeito no núcleo.
3. **Conectar primeiro a um runner passivo com mock**, em um novo entrypoint fora dos nomes legados. Não alterar o comando do Steam nem acoplar automaticamente `modprobe`, new_id, bind/unbind ou ajustes de rotação. Preparar qualquer associação de driver como decisão revisável separada. A ligação real do UHID só ocorre na fase autorizada; a implementação desta entrega permanece em memória.
4. **Alimentar movimento a partir de timestamps de entrada**, por SYN_REPORT/amostra física, e guardar o último MotionState. Renderizações e mensagens FFB frequentes não devem inventar nova velocidade a partir da mesma amostra. Usar relógio monotônico compartilhado com as timelines; o logger registra monotonic_ns, não só horário civil. Derivadas e tempo de protocolo precisam continuar separados.
5. **Executar o loop com prazos explícitos**, cuidando dos controles e respostas de Feature mesmo quando o renderer estiver ocioso. Aplicar cada evento de estado em ordem, manter somente a última amostra de força e chamar tick antes de perder a margem de renovação. Não drenar atrasos por rajadas para “recuperar” todos os níveis antigos. Close/Stop/EOF/erro/SIGTERM devem desligar e liberar recursos; SIGKILL não permite cleanup e exige a proteção independente do backend. Revisar explicitamente o limite global de teste da saída (atualmente 120 s), sem confundi-lo com duração dos efeitos.
6. **Validar reconhecimento no jogo ainda com mock**, após autorização: mesmas entradas v0.8, enumeração FFB, handshake corrigido e captura completa dos relatórios. Somente depois decidir o adaptador EVDEV real, seus limites, exclusividade, ganho/autocenter, timeout e ensaio supervisionado. Comandos proprietários Logitech continuam fora dessa integração inicial.

Critério para passar da fachada ao runner: regressão de entrada, replay offline e falhas de I/O simuladas passando, sem mudanças nas referências. Critério para considerar backend físico: captura de corrida suficiente e revisão de sinais/segurança/latência. O fato de não haver falhas esperadas na suite não substitui esses critérios.

## O que precisa de uma captura completa do DiRT 5

O corpus atual possui cinco arquivos e 132 registros de inicialização/reset/ganho. Não contém efeitos completos de corrida nem timestamps de movimento. Continua útil como regressão de handshake, sem validar a sensação de FFB.

A futura captura passiva deve preservar bytes brutos, tipo UHID, request ID, Report ID, tamanho, direção do tráfego, resultado/erro e respostas enviadas. Registrar também hash do descritor e versão do bridge, kernel, Proton/Wine, rota HIDRAW/EVDEV, cenários e amostras de posição com relógio monotônico.

| Comportamento a observar | O que permitirá validar |
|---|---|
| Startup e criação de efeitos | Tipos realmente usados, Byte Count, quantidade de blocos e aceitação do pool/RAM coerentes |
| Output versus Set Report | Rota efetiva no Proton usado, valores rtype, presença dos IDs e possíveis registros duplicados pelo logger |
| Start frequente e Loop Count | Contagens enviadas, reinícios esperados e uso de 0/255; sem presumir quirks inexistentes |
| Menu, corrida, pause, retomada e perda de foco | Sequências reais de Pause/Continue, Enable/Disable, Stop e Reset |
| Atualizações de força/envelope/condição | Duração, delays, periodicidade, flags, direções e relação temporal entre parâmetros |
| GET PID State e estado assíncrono | Se o host depende de consultas ou exige notificações Input; a entrega atual oferece somente snapshot por GET |
| Saída do jogo/desconexão | Se CLOSE/STOP são emitidos apesar de leitores persistentes do Steam/Wine; necessidade de supervisão adicional |
| Relatórios rejeitados/Custom/Ramp | Se recursos ainda não implementados bloqueiam o jogo; qualquer extensão requer decisão e testes próprios |

Deduplicação de observações entre caminhos só pode usar identidade comprovada pelo capturador. Bytes iguais não bastam: podem ser dois Start legítimos. Timestamps e proveniência devem permitir replay reproduzível sem duplicar o mesmo evento na coleta e no diagnóstico.

Separadamente, um ensaio físico futuro precisará validar polaridade entre ABS_X/EVDEV/motor, escalas e filtros de movimento, comportamento do atrito, resolução/latência, conflitos de escritores e a expiração real do replay. Nada disso foi testado fisicamente nesta entrega.
