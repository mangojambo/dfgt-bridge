# Transporte PID e ciclo de vida offline

Esta etapa encerra as seis divergências conhecidas restantes da auditoria. O pacote não abre arquivos de dispositivo e não está conectado aos launchers. `OfflineSession` aceita exclusivamente `MockBackend`; `PIDTransport` transforma bytes em chamadas ao núcleo e devolve respostas em bytes, sem fazer I/O.

## Tipos e framing

| Espaço de valores | Input | Output | Feature |
|---|---:|---:|---:|
| USB HID, byte alto de wValue | 1 | 2 | 3 |
| Enum HID interna do Linux | 0 | 1 | 2 |
| **UHID, campo rtype dos eventos** | **2** | **1** | **0** |

O adaptador recebe apenas a última linha. Não tenta adivinhar ou converter um `3` enviado como Feature USB. A separação segue [HID 1.11, 7.2.1](https://www.usb.org/sites/default/files/hid1_11.pdf), [enum HID](https://github.com/torvalds/linux/blob/master/include/uapi/linux/hid.h) e [UAPI UHID](https://github.com/torvalds/linux/blob/master/include/uapi/linux/uhid.h). O [driver UHID](https://github.com/torvalds/linux/blob/master/drivers/hid/uhid.c) faz a conversão entre suas duas enums.

| Evento recebido | Tratamento |
|---|---|
| `UHID_OUTPUT=6` | Lê data[4096], size e rtype; encaminha uma vez ao decoder correto; sem ACK |
| `UHID_SET_REPORT=13` | Lê request ID, rnum, rtype, size e data; responde `UHID_SET_REPORT_REPLY=14` |
| `UHID_GET_REPORT=9` | Consulta relatório autorizado por tipo e ID; responde `UHID_GET_REPORT_REPLY=10`, com erro, tamanho e bytes |
| `UHID_STOP=3` / `UHID_CLOSE=5` | Encerra a sessão offline, limpa estado e para/libera o slot mock |
| `UHID_START=2` / `UHID_OPEN=4` | Reconhecidos; não rearmam uma sessão encerrada |

O framing UHID usa ordem nativa de bytes e estruturas packed (`struct` com prefixo `=`). Os campos dos relatórios PID continuam little endian, separados dessa ABI. A implementação usa a UAPI com UHID_DATA_MAX=4096 e evento máximo de 4380 bytes; a futura camada real deve verificar esses contratos contra o ambiente de execução.

São aceitos eventos compactos ou preenchidos até o tamanho da união. Conforme a UAPI, dados curtos de Set Report são completados com zero; os metadados necessários para identificar a requisição precisam estar presentes. Tamanhos declarados inválidos, tipos desconhecidos, divergência entre rnum e Report ID e comprimentos PID errados geram erro. Isso é distinto de aceitar silenciosamente relatórios PID de tamanho variável. Um buffer contém exatamente um evento, não uma concatenação de mensagens.

Respostas contêm errno positivo no campo u16 e o ID original; erro de leitura retorna tamanho zero. No driver UHID consultado, erro de resposta pode virar EIO genérico para o cliente, portanto não se promete que o errno específico chegará intacto ao jogo. Quando falta até o ID, só é possível registrar o erro, sem inventar uma resposta correlacionada.

## Rotas de relatórios

- Feature Write `0x54`: Create New Effect, exatamente quatro bytes. O seletor segue a lista do descritor; Byte Count ocupa dez bits, com máximo lógico 511 e padding zero. Para tipos não Custom o tamanho é ignorado. Custom e seletores reservados conhecidos retornam Allocation Error, sem consumo de slot.
- Feature Read `0x56`: resultado da última alocação e memória disponível; não aloca nem consome o resultado.
- Feature Read `0x57`: tamanho do pool, simultâneos e flag Device Managed. Escritas nesses dois relatórios de consulta são rejeitadas.
- Output `0x50/51/52/53/55/58/59/5a/5b/5d`: mesmo decoder, tanto por UHID_OUTPUT quanto por UHID_SET_REPORT. Tipo errado não é corrigido por inferência.
- Input Read `0x50`: snapshot do estado PID, distinto do Output `0x50` de Device Control. Input `0x01` continua pertencendo à futura camada da v0.8; não é sintetizado aqui.

No PID State congelado há Report Count=8 com somente três usages de flags. O [parser HID do Linux](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-core.c) repete a última usage. Assim, Effect Playing é espelhado nos bits 2..7; eles não são tratados como padding constante. O byte de flags de um efeito ativo habilitado é `0xfe`; pausado/habilitado é `0x03`. O índice é o menor efeito em execução, ou 1 quando nenhum toca, mantendo o intervalo declarado. Este é um snapshot de consulta; notificações assíncronas de estado ainda não são emitidas.

## Pool

Capacidade padrão 40, configurável em 1..40 sem ultrapassar a faixa do descritor. Aloca sempre o menor ID livre; IDs ocupados permanecem estáveis, sem compactação. Block Free libera somente o alvo. Atualização/Start/Free de bloco ausente é erro, nunca alocação implícita.

Cada slot reserva 64 bytes de orçamento lógico, resultando em 2560 bytes no padrão. É um modelo de recursos do dispositivo virtual, não RAM física do DFGT ou tamanho de objetos Python. `0x57` informa o total e `0x56` a quantidade atual livre. Success=1, Full=2, Error=3; em falha o índice é zero e não deve ser usado. Pool cheio e tipo não suportado recebem ACK de transporte normal: o resultado é consultado por `0x56`, conforme o fluxo Device Managed descrito no [USB PID, 5.11.2](https://www.usb.org/sites/default/files/pid1_01.pdf).

Reset limpa blocos e resultado pendente. Os testes conferem o contrato de capacidade diretamente no descritor v0.8 preservado. As fixtures antigas continuam idênticas: os valores históricos RAM=0xffff são verificados contra a referência antiga e os novos valores contra vetores exatos do pool real. Não se passou a reproduzir como correta uma resposta que a auditoria identificou como fictícia.

## Identidade e deduplicação

Uma mensagem usa uma única rota de despacho; Set Report não chama duas APIs que apliquem a mesma operação. Requisições de controle repetidas com o mesmo tipo de evento, ID e conteúdo retornam o resultado anterior. Reutilizar o ID com conteúdo diferente é erro. Os caches LRU têm 128 entradas por padrão, configuráveis até 4096, e são limpos ao encerrar a sessão.

UHID_OUTPUT não possui request ID, nem um identificador compartilhado com Set Report. Dois Start idênticos podem representar reinícios legítimos. Por isso não se deduplicam bytes iguais por tempo ou por origem. Quando um replayer/observador **comprova** que duas observações representam uma única operação, pode fornecer o mesmo `observation_id`; dentro da janela do cache, ela é aplicada uma vez entre caminhos. Não existe promessa de deduplicação infinita após eviction. O futuro leitor de UHID deve processar cada evento somente uma vez e não reinjetar também a cópia de diagnóstico.

## Timeline e controles

O relógio de execução é lógico, independente da hora de chegada dos comandos. Pause congela esse tempo; Continue desconta a pausa e preserva atraso, envelope, fase e repetições restantes. As operações são idempotentes quando já no estado solicitado. Start durante Pause começa nessa posição lógica e passa a avançar após Continue. Disable Actuators só silencia a saída: não apaga efeitos nem pausa o tempo.

Start sempre reinicia a timeline do alvo; Start Solo também para os demais. Stop preserva a alocação, Free a remove e Stop All preserva o pool. Device Reset limpa efeitos, pausa e ganho, e habilita os atuadores virtuais; não rearma uma sessão encerrada pelo processo.

Loop Count do descritor é unsigned de oito bits: 1..254 executam esse número de ciclos, **255 é infinito**, e **zero executa zero ciclos**. O marcador máximo vem da definição de INFINITE do [USB PID, 1.4 e 5.9](https://www.usb.org/sites/default/files/pid1_01.pdf), não de um hábito de bibliotecas de jogos. A [API EVDEV via hid-pidff](https://github.com/torvalds/linux/blob/master/drivers/hid/usbhid/hid-pidff.c) traduz playback zero em Op Stop, não em Start com contagem zero.

Start Delay é aplicado uma vez após o comando; cada ciclo tem sua duração e reinicia envelope/fase. Ciclos finitos terminam exatamente após delay+N×duração. Duração infinita não alcança o próximo ciclo; duração zero encerra sem loops de CPU, mesmo com contagem infinita. A aritmética usa nanossegundos inteiros relativos ao Start e calcula o ciclo diretamente, sem percorrer todas as repetições de um salto temporal. Update não reinicia nem revive um efeito expirado.

## Saída e encerramento

O nível final já quantizado é comparado com o pedido carregado. Só há upload se nível, duração do replay ou direção mudarem, ou se ainda não houver slot. Play/renovação são separados: força estável renova a cada pelo menos metade dos 160 ms de replay, sujeita ao tick normal, sem reupload. A frequência continua dependente de o chamador executar ticks; não há thread ou scheduler oculto.

O limite global de teste supervisionado de `ConstantOutput` continua em 120 s a partir da primeira saída, configurável no intervalo herdado de 30..240 s. Ele desliga a política de saída e exige nova instância; não altera a duração PID nem prova que um efeito infinito terminou. A futura integração deve decidir explicitamente a política de duração da sessão, preservando as proteções, antes de uso prolongado.

Stop, zero, pausa, reset e redução de teto preservam prioridade imediata. Um Start após Stop pode aguardar o próximo tick normal; o estado não é perdido e não existe fila de níveis antigos. Se o replay expirar numa lacuna, a retomada parte de zero pelo slew, usando o valor atual. `writes` conta uploads; `play_requests` e `renewals` contam separadamente reprodução e renovação.

O mock mantém um slot e histórico limitado a 4096 ações, com contador de descarte. Esse histórico não é uma fila de comandos pendentes. O deadline é consultável independentemente do controlador: testes simulam desaparecimento do processo sem executar close/tick e observam saída zero após a expiração. Isso **não comprova** timeout físico no kernel/firmware.

`OfflineSession` processa estado e renderiza a composição após cada evento, além dos ticks explícitos. Gerencia fechamento idempotente e cleanup em exceções; não suprime a exceção. Os testes incluem bloqueio de backend não mock, consumidores encerrados, erros de renderização, alto volume, múltiplos efeitos e os cinco CSVs reais. O núcleo é serializado por um chamador; não é uma API concorrente de múltiplas threads.

Limites de escopo: não há Custom, dados de força personalizados, gatilhos de botão ou adaptação de playback a Sample Period. Os campos de gatilho/amostragem são aceitos dentro dos limites declarados, mas a reprodução atual começa por Start explícito e segue os ticks do chamador. O descritor Ramp unsigned também continua limitando rampas signed. Esses recursos não faziam parte das seis divergências e exigirão decisão se aparecerem na captura real.
