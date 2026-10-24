# Runner passivo e captura reproduzível

Implementado em `fix/ffb-core`. Duas capturas reais fornecidas pelo operador foram analisadas offline em [CAPTURE_COMPARISON_20261024.md](CAPTURE_COMPARISON_20261024.md). O runner cria o G29 virtual e lê o DFGT quando iniciado pelo operador. Toda força calculada termina exclusivamente em `MockBackend`; não existe opção de backend físico. Os launchers históricos não foram redirecionados.

## Execução no SteamOS

Python 3.10+ e o ambiente UHID/driver que já permitia a v0.8 são os pré-requisitos. Não é necessário instalar `evdev`. Não executar simultaneamente o runner novo e a baseline/experimentos.

Na raiz do projeto, iniciar somente o runner passivo:

```sh
cd /home/deck/dfgt-bridge
sudo /usr/bin/python3 -B scripts/run-passive.py
```

O único fd do DFGT é aberto com `O_RDONLY`. As consultas são EVIOCGID, EVIOCGABS, EVIOCGKEY e a seleção de CLOCK_MONOTONIC **para esse leitor** via EVIOCSCLOCKID. Não há EVIOCSFF, EVIOCRMFF, EV_FF, grab, escrita USB, autocenter, ajuste de rotação, modprobe ou bind. A escrita de relatórios ocorre somente no fd separado de `/dev/uhid`. [UAPI input](https://github.com/torvalds/linux/blob/master/include/uapi/linux/input.h), [contrato UHID](https://docs.kernel.org/hid/uhid.html).

Capturar uma corrida completa, incluindo inicialização e saída:

```sh
sudo /usr/bin/python3 -B scripts/run-passive.py \
  --gas-axis y \
  --capture "captures/dirt5-passivo-$(date +%Y%m%d-%H%M%S).jsonl.gz" \
  --proton 'INFORMAR VERSÃO EXATA' \
  --scenario 'INFORMAR carro, pista, clima e ajustes FFB do jogo' \
  --hid-route unknown
```

O arquivo é criado exclusivamente, nunca sobrescrito. Se existir, a mensagem preserva o erro e sugere um nome alternativo disponível, sem criá-lo automaticamente. `.jsonl.gz` comprime em streaming sem remover bytes; `.jsonl` também funciona. Capturas completas podem ser grandes, especialmente sem gzip, e permanecem ignoradas em `captures/`. Evite redirecionar stdin se quiser marcar as fases pelo console.

Depois de iniciar o runner, abrir o jogo usando a configuração já existente. No terminal do runner, os comandos são:

| Comando + Enter | Ação |
|---|---|
| `init` | Marca inicialização/atividade ainda sem atribuição |
| `menu` | Marca menu observado pelo operador |
| `race` | Marca início da corrida observado pelo operador |
| `strength 0..60` | Altera imediatamente o ganho/teto **da simulação** |
| `summary` | Imprime resumo acumulado |
| `quit` ou Ctrl+C | Encerra, registra cleanup e remove o UHID |

Marcar `menu` ao entrar no jogo, `race` ao começar a corrida e `menu` depois de terminá-la. Incluir pausa/retomada, perda de foco e saída do jogo na coleta. Encerrar o runner por `quit` ou Ctrl+C para gravar o footer. `--duration N` permite uma duração máxima opcional; não há limite global de 120 s no mock desta sessão.

UHID não fornece PID de processo nem uma etiqueta “DiRT 5”. Todos os relatórios mantêm `producer=unattributed`; as fases são anotações explícitas do operador, não prova de autoria. Comandos de driver podem aparecer durante qualquer fase. `--proton`, `--scenario` e `--hid-route` também são anotações; não se infere rota hidraw/evdev somente pelo rtype. O resumo separa contagens por fase para investigar probes sem descartá-los.

O diagnóstico periódico inclui associação de driver e capacidades FF virtuais lidas em sysfs, eventos/s, OUTPUT/SET/GET, efeitos por tipo, ativos/pausados/liberados, falhas do pool, rejeições brutas, classificação separada de comandos proprietários ignorados e falhas PID/transporte, componentes de força, resultante, clipping, atrasos e ressincronizações. Exibe também ABS_X/Y/Z físicos e X/Z/Rz/Y virtuais, eixo escolhido e inversão. Há uma linha por segundo e um resumo completo ao sair. `summary` é uma impressão adicional solicitada pelo operador.

O runner **não corrige associação de driver**. A identidade G29 pode selecionar um driver Logitech em vez de um driver PID; a configuração que fazia a v0.8 reconhecer FFB precisa ser conferida na coleta. Ausência de tráfego PID ou driver inadequado não é sucesso. Os campos `binding` e `ff_capabilities` ajudam a diagnosticar isso, sem modificar SteamOS/udev/Steam.

## Contrato de entrada e transporte

- Descritor, nome, BUS_USB, VID/PID `046d:c24f`, versão `0x0110` e bytes de Input `0x01` são comparados com a v0.8 por extração AST offline. O runner não importa nem executa referências.
- Direção ABS_X 0..16383 vira X16; acelerador ABS_Y e freio ABS_Z preservam 0..255. **Acelerador em Y por padrão no CLI**, correspondendo à configuração funcional v0.8 do DiRT 5; `--gas-axis z/y/both` e `--invert-gas` continuam disponíveis. As APIs de baixo nível conservam o default legado Z para compatibilidade; o runner passa sua configuração explicitamente. D-pad e os 25 botões mantêm seus bits.
- Inicialmente lê os três eixos; hat neutro e conjunto de botões vazio repetem a v0.8. SYN_DROPPED descarta eventos até SYN_REPORT e consulta o estado atual, evitando controles presos.
- Timestamps de SYN_REPORT monotônicos alimentam o estimador; eventos FFB e ticks reutilizam a amostra. Derivadas expiram após 250 ms. Posição e relatórios de controles continuam sendo transmitidos. O alvo ABI de entrada é SteamOS Linux x86_64.
- Somente `PIDTransport.handle()` aplica um evento. Não há respondedor legado paralelo. Retries com request ID equivalente são deduplicados; dois OUTPUT idênticos continuam sendo duas operações legítimas. Não existe correlação comprovável entre OUTPUT e SET_REPORT somente pelos bytes.
- Respostas GET/SET preservam request ID, rtype e Report ID e precedem o registro em disco. GET Input `0x01` lê o último input; PID State `0x50` é emitido em transições. GET Input `0x50`, Feature `0x54/56/57` e Outputs usam os contratos em [PID_TRANSPORT.md](PID_TRANSPORT.md).
- CLOSE limpa efeitos/caches e silencia o mock, mas permite novo OPEN; STOP termina a sessão. Erros, EOF e sinais tratados fecham entrada, mock e UHID. Fechar o fd UHID destrói o dispositivo mesmo se a escrita DESTROY falhar. SIGKILL não permite footer/cleanup Python; nenhum motor físico está conectado.

## Captura JSONL v1

Cada linha é um objeto JSON, opcionalmente dentro de gzip. `schema=dfgt-bridge-passive`, `version=1`.

| Registro/campo | Conteúdo |
|---|---|
| Header / metadata | Origem monotônica ns; descritor completo e SHA-256; identidade; configuração; hash dos fontes Python; byte order; kernel/Python; Proton, rota e cenário informados; backend mock |
| Step / timestamp_ns | Chegada monotônica inteira em nanossegundos; relógio do núcleo = `(timestamp_ns-origin_ns)/1e9` |
| Step / action,data | UHID completo em `raw_hex`, entrada completa com `sample_ns`, tick, ganho, fase ou encerramento |
| Result / info | Origem UHID, tipo de evento, rtype, Report ID, request ID, tamanho declarado, pacote completo disponível e bloco afetado |
| Result / transitions,state_sha256 | Campos alterados, blocos criados/liberados, hash do estado completo e PID State |
| Result / replies_hex,writes_hex | Respostas e notificações completas, na ordem produzida |
| Result / components,renderer_total | Força por família e soma antes da política de saída |
| Result / mix,final_force,mock | Ganho/teto/clipping antes de slew, força simulada final, slot e deadline independentes |
| Result / mock_actions | Upload, play, stop e erase efetivamente feitos no mock, em ordem |
| Result / error,reason,renderer_ignored | Rejeição, duplicação, clipping, gate/slew ou condição de eixo não suportado; sem ocultar descartes |
| Step / delivery | Quantas escritas virtuais completas foram concluídas e eventual erro de I/O |
| Step / timing | Tempo real de processamento e entrega em ns; diagnóstico, sem dirigir o relógio do replay |
| Step / classification | Anotação opcional v1: categoria e comando conhecido; mantém erro bruto, sem executar protocolo proprietário |
| Footer | Completude, resumo determinístico, causa da saída, driver/capacidades e métricas de latência observadas |
| Todos / seq,previous,sha256 | Sequência e encadeamento SHA-256 para detectar perda, duplicação, reordenação e alteração acidental |

Os bytes UHID brutos incluem header e padding recebidos, não só o payload PID. Dados curtos/malformados permanecem como chegaram. `packet_hex` não inventa bytes ausentes; eventual zero-extension pertence ao transporte e pode ser reproduzida a partir do evento bruto. O SHA-256 do estado inclui todos os parâmetros de efeito, mesmo quando só mudanças aparecem em `transitions`.

Os buffers são limitados: 32 eventos UHID por passagem, 64 input_events por leitura, console de 1024 bytes, caches do transporte e histórico mock limitados. Não há thread, fila de forças ou recuperação de ticks antigos em rajadas. Captura é síncrona/bufferizada; armazenamento lento pode aumentar latência, que fica registrada. O ACK é enviado antes da serialização/escrita do registro. Uma escrita UHID parcial é fatal, sem reenviar a cauda ou reaplicar a operação. EAGAIN tem limite de 250 ms. Linha de captura possui limite de 2 MiB; não se trunca silenciosamente para obedecê-lo.

## Replay e testes offline

```sh
python3 -B scripts/replay-capture.py captures/NOME-DA-CAPTURA.jsonl.gz
python3 -B -W error::ResourceWarning scripts/test-offline.py
```

Replay não abre dispositivos nem importa as classes de I/O. Recria `PassiveSession` com configuração e relógio gravados, usa o mesmo transporte/decoder/pool/renderer e compara **todas** as respostas, notificações, mudanças, hashes de estado, componentes, forças e ações do mock. Mede também consistência de entrega e resumo final. Hash dos fontes e contrato do descritor precisam coincidir no modo estrito. Para fontes diferentes, pode-se fazer checkout da implementação original ou usar explicitamente `--verify-compatible`: esse modo permite somente hash de fontes diferente, mantendo todas as verificações semânticas e marcando `implementation_match=false`. Só retorna `compatibility_verified=true` depois de conferir a captura completa. Não ignora divergências e não altera o eixo registrado. As classificações opcionais são verificadas quando presentes. As medições de latência são verificadas como metadados, não reexecutadas como tempo de CPU.

Saída CLI: 0 = replay completo conferido; 1 = erro de integridade/semântica/formato/I/O; 2 = prefixo coerente, porém captura incompleta ou entrega com falha. Ausência de footer nunca equivale a corrida validada. Linha ou gzip truncados podem impedir validar o último prefixo e produzir código 1. Um replay completo sem forças, sem alocações ou sem fase de corrida também não comprova FFB do jogo; verificar contagens e `nonzero_force_samples`.

A fixture `tests/fixtures/passive_race.json` é **sintética**: reproduz dez minutos lógicos com Constant/Spring/Sine simultâneos, probes, pool, pausa, reinício, ganho e saída. Não é uma captura do DiRT 5. Os cinco CSVs originais e seus 132 registros continuam preservados e não ganharam atribuição de “corrida completa”.

## Comparação e critérios para o próximo estágio

| Item | Offline | Coleta real pendente |
|---|---|---|
| Descritor/identidade/criação UHID | Bytes iguais à v0.8 | Enumeração e driver associado |
| Direção, acelerador, freio | Ranges, extremos e opções iguais | Calibração/seleção no jogo |
| Botões e D-pad | Todos os bits e direções iguais | Mapeamento do jogo |
| Menu FFB | Feature/pool/estado/respostas simulados | Reconhecimento pelo DiRT 5/Proton |
| Corrida e ciclo de vida | Replay e vetores determinísticos | Tipos, flags e loops realmente usados |
| Notificação PID State | Ordenação e transições testadas | Aceitação/necessidade no driver/Proton |
| Latência e armazenamento | Limites e falhas testados com fakes | Taxa sustentada e eventuais perdas reais |

Antes de conectar um backend físico: obter captura representativa com fases, posição e parâmetros do jogo; conferir replay completo e respostas aceitas; investigar todo erro/condição ignorada; avaliar sinais/escalas/filtros nas forças simuladas; revisar polaridade mecânica, exclusividade do escritor, autocenter, limites e emergência; definir deadline físico independente, duração da sessão e ensaio supervisionado com aprovação explícita. Custom, gatilhos e Sample Period continuam limitados conforme o contrato do núcleo. Nada nesta entrega valida o protocolo proprietário G29/DFGT ou autoriza substituí-lo por comandos Logitech.
