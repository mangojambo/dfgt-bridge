# Plano de conexão à baseline v0.8 — não executado

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
