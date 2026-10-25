# Comparação das capturas reais e regressão do acelerador

Análise de 24/10/2026, antes de qualquer mudança no código. Foram localizados os dois `.jsonl.gz` de `captures/` por mtime. O mais recente é `dirt5-20261024-142711.jsonl.gz` (17:28:32 UTC); `dirt5-passivo-01.jsonl.gz` é o anterior (17:24:16 UTC). Ambos permanecem intactos e ignorados pelo Git. Não foi aberto nenhum dispositivo durante esta investigação.

## Preservação e replay

| Captura | SHA-256 do gzip original |
|---|---|
| `dirt5-passivo-01.jsonl.gz` | `a7391b6420a89946999a7f23ad00826fcc8fd3f67785fdf269074ead4b93ae95` |
| `dirt5-20261024-142711.jsonl.gz` | `49604c60364427d2d70e810948ae4064a8a4cf3bcccadf871982d82568631b5f` |

Antes das correções, os dois comandos abaixo terminaram com código 0, `verified=true`, `complete=true`, zero erros de entrega e nenhuma divergência de respostas, estados, força ou resumo:

```sh
python3 -B scripts/replay-capture.py captures/dirt5-20261024-142711.jsonl.gz
python3 -B scripts/replay-capture.py captures/dirt5-passivo-01.jsonl.gz
```

Os headers registram a mesma implementação `929f4f828611e38a75f99b0668e3d88036ee3fb897bfef1030c251f7a530a6b9`, descritor `453b7bf7084a91b6d67200039bdebee09d483e27be8c693f9f1aab6dab998e3c`, fonte `046d:c29a`, virtual `046d:c24f`, `gas_axis=z`, `invert_gas=false`, intensidade mock 20% e pool 40. Ambos os footers registram `hid-universal-pidff`, saída SIGINT, zero ressincronizações, zero ticks perdidos e zero ações lentas segundo o limite de 10 ms do runner. Proton e rota permanecem `unknown`; nenhuma fase foi marcada pelo operador. Portanto não é possível atribuir cada pacote a um processo ou separar menu/corrida por marcadores.

Depois das correções, o hash dos fontes mudou. O modo estrito continua recusando hash diferente. Para conferir as capturas antigas com a implementação atual:

```sh
python3 -B scripts/replay-capture.py --verify-compatible captures/dirt5-20261024-142711.jsonl.gz
python3 -B scripts/replay-capture.py --verify-compatible captures/dirt5-passivo-01.jsonl.gz
```

Ambos passaram novamente, com `compatibility_verified=true`. Essa opção flexibiliza somente igualdade do hash dos fontes: preserva validação de checksum/ordem, descritor, configuração, semântica e **todos** os resultados gravados. Não altera metadados originais nem força o replay em Y: o `z` explicitamente registrado continua sendo reproduzido. Novas anotações de classificação, quando presentes, também são conferidas. Um resultado divergente continua sendo erro, mesmo nesse modo.

## PID e força calculada

| Métrica | Anterior: passivo-01 | Mais recente: 142711 |
|---|---:|---:|
| Duração total (s) | 189,094 | 80,318 |
| Ações verificadas | 35.723 | 37.280 |
| Eventos UHID | 19.670 | 21.554 |
| OUTPUT / SET_REPORT / GET_REPORT | 19.651 / 8 / 7 | 21.535 / 8 / 7 |
| Alocações / liberações | 6 / 6 | 6 / 6 |
| Máximo de blocos simultâneos | 5 | 5 |
| Start PID, Op=1, Loop Count=1 | 15.405 | 17.163 |
| Stop PID, Op=3, Loop Count=0 | 11 | 11 |
| Plays pedidos ao mock / renovações | 1.072 / 101 | 1.380 / 156 |
| Uploads simulados | 969 | 1.223 |
| Pico final mock | 6.553 | 6.553 |
| Soma máxima do renderer, antes de ganho/teto | 51.861,225 | 54.065,175 |
| Ocorrências de clipping registradas | 172 | 89 |
| Quadros de entrada, incluindo inicial | 9.594 | 12.731 |
| Rejeições brutas `unsupported_report` | 16 | 16 |
| Falhas de entrega / alocação | 0 / 0 | 0 / 0 |

Os 1.072 plays da primeira captura são comandos do backend mock, não a quantidade de Start PID do jogo. Da mesma forma, 6.553 é o pico depois do teto de 20%, não o pico bruto do renderer. Não há fila de forças sendo reproduzida para justificar essa diferença: uploads e renovações são deduplicados e limitados pela política já existente.

Em ambas as sessões, uma alocação Constant inicial é liberada; depois aparecem Constant, Spring, Damper, Friction e Sine, nos blocos 1..5. IDs permanecem dentro do pool. Atualizações de Constant (`0x55`) são 1.039/927, Set Effect (`0x51`) 132/144, Condition (`0x53`) 3.032/3.258, Periodic (`0x5d`) 2/2. Create Feature (`0x54`) e consultas Block Load (`0x56`) têm seis ocorrências cada; Pool (`0x57`) é consultado uma vez. Device Control contém cinco Reset (`5004`) e um Stop All (`5003`) por captura; não há Pause/Continue nesses arquivos.

Start se repete 5.135/5.721 vezes para cada um de Constant, Spring e Sine. Damper e Friction recebem uma configuração de condição cada, mas **não recebem Start**; há somente Stop para esses dois blocos. Seus componentes calculados em zero são coerentes com essa sequência, não evidência de falha de leitura da velocidade. Não foi necessário alterar a matemática.

Os picos brutos por família são Constant=32.767 e Spring=21.298,175 em ambas; Periodic=1.763,459/1.117,400. Não há Inertia ou Ramp criados. Todos os Starts usam uma repetição; não há evidência nova sobre Loop Count 0/255. Reinícios frequentes são preservados, sem deduplicar OUTPUTs iguais. Nenhum erro de bloco inexistente, família incompatível, framing, rtype, request ID ou pool cheio foi observado. Os 16 erros brutos do contador de transporte pertencem integralmente aos pacotes Logitech abaixo.

## Os 16 rejeitados, identificados individualmente

Todos chegam como `UHID_OUTPUT=6`, `rtype=1`, sete bytes, sem request ID. Na coluna Report ID genérica aparece o primeiro byte do pacote, mas neste protocolo legado ele é um **opcode Logitech**, não um Report ID PID anunciado pelo descritor virtual.

| Pacote completo | Categoria | Semântica reconhecida | Seq. anterior | Seq. mais recente |
|---|---|---|---|---|
| `f5000000000000` | Proprietário Logitech conhecido | Desativar autocenter | 13, 3377, 3394, 3513, 3529 | 13, 123, 138, 258, 273 |
| `0d000000000000` | Proprietário Logitech conhecido | Desativar fixed loop | 3389, 3390, 3391, 3392, 3393, 3524, 3525, 3526, 3527, 3528 | 133, 134, 135, 136, 137, 268, 269, 270, 271, 272 |
| `f8120000000000` | Proprietário Logitech conhecido | Máscara de LEDs zero | 3531 | 276 |

Logo, em **cada** captura: 16 proprietários conhecidos, zero PID válido rejeitado incorretamente, zero desconhecidos e zero malformados entre esses 16. Nenhum desses pacotes configura um efeito PID nos blocos virtuais.

As correspondências de autocenter e LEDs estão em `lg4ff_set_autocenter_default()` e `lg4ff_set_leds()` do [hid-lg4ff upstream](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-lg4ff.c). O vetor de fixed loop aparece em `lg4ff_init_slots()` do [new-lg4ff](https://github.com/berarma/new-lg4ff/blob/master/hid-lg4ff.c). Isso identifica a família e a intenção do comando; não prova qual processo o emitiu, nem autoriza sua transmissão ao DFGT.

O novo diagnóstico rotula somente esses três vetores exatos como `proprietary_ignored`, com nomes separados. Eles continuam sem execução, sem alocação e sem upload. O erro bruto `unsupported_report` e seu contador são conservados para transparência e replay; o diagnóstico apresenta a classificação separadamente dos erros PID/estado/transporte. Comandos desconhecidos, comprimentos errados e parâmetros diferentes não são aceitos por semelhança de prefixo. Nenhum opcode novo foi implementado.

## Causa exata do acelerador

O runner reproduziu a função `report()` corretamente **para os argumentos usados**. O defeito foi transportar o padrão histórico da função (`z`) para o CLI novo e omitir no comando preparado o parâmetro que fazia parte da configuração funcional: `--gas-axis y`.

Foram comparados todos os **22.325** relatórios Input capturados com a v0.8 por extração AST, sem executar/importar seu entrypoint. Houve zero diferenças com a v0.8 configurada em Z. Em relação à configuração funcional em Y, diferem 2.274 quadros da captura anterior e 1.680 da mais recente. Os demais têm acelerador 255, situação em que os dois campos são `ff` e a regressão fica invisível em um teste apenas com pedais soltos.

| Campo | Origem e transformação preservadas | Offset no relatório, incluindo ID |
|---|---|---:|
| Report ID | `01` | 0 |
| Direção X16 | ABS_X 0..16383 → `round(x*65535/16383)`, clamp 0..65535, little endian | 1..2 |
| Z | 255 no perfil funcional Y; recebe ABS_Y somente na alternativa Z/both | 3 |
| Freio Rz | ABS_Z 0..255, sem inversão | 4 |
| Acelerador Y | ABS_Y 0..255 no perfil Y/both; 255 quando selecionado Z | 5 |
| D-pad / botões | Layout v0.8 preservado | 6 / 7..10 |

Os pedais não passam por normalização float nem troca de eixos físicos. Na convenção usada, 255 é solto, aproximadamente 128 é meio curso e 0 é pressionado. `invert_gas=false` é mantido; a opção explícita de inversão faz somente `255-ABS_Y`, sem modificar freio. Seleção física permanece pelo caminho by-id original, validando `046d:c29a` e ranges antes de aceitar a entrada. Identidade virtual permanece G29.

Evidência da captura mais recente, seq. 4822, estado `[8161, 0, 255, 0, 0, []]`:

```text
capturado em Z:    01 85 7f 00 ff ff 08 00 00 00 00
v0.8 funcional Y:  01 85 7f ff ff 00 08 00 00 00 00
                           Z  Rz  Y
```

O ABS_Y físico percorreu 0..255 nas duas capturas (179/225 valores distintos); o Y virtual permaneceu 255 em todos os 9.594/12.731 quadros. O freio percorreu 0..255 e foi transmitido corretamente. A direção percorreu 0..10234 na anterior e todo 0..16383 na mais recente. Portanto não faltam amostras do acelerador: elas foram colocadas no campo errado para a configuração do jogo relatada pelo operador.

Correção: **CLI do runner passa a usar Y por padrão**, conservando `--gas-axis y/z/both` e `--invert-gas`. A função de baixo nível e a sessão em memória preservam seus defaults legados; o runner sempre fornece explicitamente sua configuração. Nenhum descritor, rotina de leitura, referência histórica ou fórmula FFB foi alterado.

## Regressões e próximo teste passivo

`tests/fixtures/dirt5_passive_regression.json` contém 13 amostras reais identificadas por arquivo/hash/seq e as 32 rejeições dos dois arquivos. `captured_z_hex` é observado; `expected_v08_y_hex` é uma previsão offline da referência, não uma nova captura física. Os testes adicionam vetores dourados para direção, acelerador solto/50%/pressionado, freio, ambos os pedais, opções, CLI e caminho desde input_event simulado até os bytes UHID completos. A classificação possui casos desconhecidos/malformados e verificação de que não altera efeitos.

**241 testes passam, zero falhas esperadas e zero ignorados**, também com `scripts/test-offline.py` bloqueando opens reais de dispositivos. As referências e as duas capturas foram conferidas por hash. Os fontes de `ffb/` não receberam alterações nesta etapa.

Comando preparado, não executado nesta investigação:

```sh
cd /path/to/dfgt-bridge
sudo /usr/bin/python3 -B scripts/run-passive.py \
  --gas-axis y \
  --capture "captures/dirt5-y-$(date +%Y%m%d-%H%M%S).jsonl.gz" \
  --proton 'INFORMAR VERSÃO EXATA' \
  --scenario 'DiRT 5 - acelerador Y, pedais solto/meio/fundo e simultâneos'
```

O runner já tem identidade G29 fixa e não aceita nem necessita `--identity g29` ou `--auto-pidff`. Não associa drivers; usa a configuração existente. No console, marcar `menu`, verificar Y variando 255→128→0 com Z=255 e Rz acompanhando o freio, marcar `race`, testar os controles, retornar a `menu` e encerrar com `quit` ou Ctrl+C. A aceitação do acelerador no jogo ainda depende desse próximo teste do operador; a equivalência dos bytes foi validada offline.

Tentar reutilizar um nome gera FileExistsError explicado em português e sugestão de caminho livre, sem sobrescrever ou criar automaticamente o nome alternativo. A saída continua exclusivamente mock. Não houve teste físico, alteração de driver/SteamOS ou push.
