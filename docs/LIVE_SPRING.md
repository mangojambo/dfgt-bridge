# Spring em tempo real: F5 / F6

Com o jogo fechado, instalar a versão de desenvolvimento:

```sh
cd ~/dfgt-bridge
sudo ./install.sh
```

Depois, abrir o DiRT 5 normalmente pela Steam. Não há novos argumentos de
lançamento, edição de JSON ou reinstalação entre seleções.

| Tecla | Ação |
|---|---|
| F5 sem modificadores | Selecionar Spring ×3 |
| F6 sem modificadores | Selecionar Spring ×4 |
| F7 / F8 | Intensidade geral −/+5 pontos, como antes |
| F9 | Parada imediata com latch, antes das outras teclas do mesmo lote |
| F10 | Diagnóstico da calibração, força comandada e saturação |

São seletores diretos: repetir F6 mantém ×4 e repetir F5 mantém ×3.
Autorepeat EV_KEY=2, releases e keydowns duplicados sem release não geram
seleção. O estado inicial das teclas/modificadores é consultado com EVIOCGKEY
readonly; não há grab ou injeção. Os valores de EV_KEY seguem a
[documentação Linux](https://docs.kernel.org/input/event-codes.html#ev-key).

## Aplicação e transição

O listener já pertence ao worker físico privilegiado. A seleção vai
diretamente à política nesse processo, antes de consumir outro frame de
força. Não depende da GUI, console, foco de janela ou recriação do G29.
O produtor envia componentes PID sem a seleção temporária aplicada;
somente o worker calcula o multiplicador interpolado.

Uma mudança de alvo inicia interpolação linear de **300 ms**. Outra seleção
durante a rampa parte do multiplicador usado no último cálculo válido,
sem extrapolar um valor ainda não renderizado. Selecionar novamente o mesmo
alvo não reinicia a duração. Só frames válidos e recentes podem levar esse
ganho ao mixer e renovar o efeito; seleção e poll nunca reproduzem força antiga.
O slew de 327670 unidades/s e o teto EVDEV continuam depois da mistura.

Exemplo **sintético**, Spring PID −9000, outras famílias em zero, ganho
global 100%, polaridade −1, saída inicialmente estabilizada em ×3 e frames
novos a cada 25 ms:

| Tempo desde F6 | Ganho | Spring após calibração | Alvo EVDEV |
|---:|---:|---:|---:|
| 0 ms | 3.00 | −27000 | +27000 |
| 75 ms | 3.25 | −29250 | +29250 |
| 150 ms | 3.50 | −31500 | +31500 |
| 225 ms | 3.75 | −33750 | +32767, com clipping |
| 300 ms | 4.00 | −36000 | +32767, com clipping |

A tecla sozinha não envia um novo comando. Esses valores não medem torque.

### Sincronização e segurança

Um indicador compartilhado, monotônico de 0 para 1, é publicado pelo worker
antes da primeira seleção. Depois disso, o produtor não usa o ganho salvo no
perfil para decidir cancelamentos entre famílias: ele não conhece o ganho
transitório exato. Cancelamentos são avaliados pelo worker nos frames frescos.
Isso evita uma falsa barreira calculada com ×2 enquanto a saída usa ×3/×4.

PID Stop/Reset/Disable/Pause/Free/Close mantêm suas barreiras independentes.
Frames com todas as famílias em zero também mantêm a barreira, mesmo quando
coalescidos/substituídos antes de enviar. Um ponto de cancelamento entre
efeitos ativos não equivale a um comando PID de parada. Não forma fila de
forças para reprodução posterior.

F9 e perda de teclado são processados antes de F5/F6/F7/F8/F10 do mesmo lote.
F5/F6 durante latch podem preparar outro alvo, mas não habilitam a saída.
O rearme continua explícito, com cálculo posterior válido. Hard faults são
terminais. Watchdog, épocas, supervisão do jogo e guards EVDEV permanecem;
atuadores desabilitados e efeitos parados continuam em zero.

Não há gravação de perfil. Toda sessão nova começa no Spring configurado:
×3 por padrão ou o valor personalizado explícito. Os demais ganhos,
mapeamentos de volante/pedais/botões, descritor e duração ficam preservados.

## Log e F10

Cada pressionamento reconhecido emite uma linha **[FFB CALIBRATION]**, mesmo
se o alvo já estiver selecionado. Registra tecla, ganho anterior, solicitado
e aplicado ao cálculo, origem/início/estado da rampa, duração, `changed`,
estado de saída/jogo/latch/hard fault e timestamp monotônico de processamento.
Não há log por frame ou captura automática.

| Campo do F10 | Significado |
|---|---|
| `spring_calibration.profile` | Ganho carregado no início da sessão |
| `selected` | Alvo temporário escolhido |
| `applied` | Multiplicador do último cálculo aceito pelo supervisor físico |
| `interpolated`, `transition_progress` | Posição da rampa no relógio, sem comando implícito |
| `command_gain` | Ganho associado ao último comando ativo; `null` após parada |
| `force_before`, `force_after` | Spring calculado antes/depois da calibração, antes de mistura/teto/slew |
| `evdev_level`, `io.level_commanded` | Nível final solicitado e nível acompanhado pelo adaptador EVDEV |
| `force_pipeline.user_gain`, `io.gain_commanded` | Ganho global de software e FF_GAIN comandado |
| `io.level_uploaded` | Último upload; pode existir mesmo sem play |
| `latched`, `game_running`, `zero_reason` | Estado da supervisão |

`applied` pode avançar em um cálculo retido no gate de taxa, enquanto
`command_gain` continua no comando anterior. Durante latch pode haver cálculo
não zero, mas saída zero, ganho físico desligado e nenhum play. Nenhum desses
campos é medição de torque ou readback de firmware.

### Estatísticas de comando

O campo legado `clipped` no topo do F10 continua contando ticks da política.
Para comparar comandos EVDEV, usar `command_statistics`, não somar esse campo
legado às novas contagens.

`command_statistics` conta comandos de força de ações concluídas com sucesso:
play/renovação e transições para parado. Upload isolado, SYN, escritas de
ganho/autocenter e frames descartados/retidos no gate não acrescentam comando.
Após erro de I/O não se inventam comandos cujo resultado é incerto.

- `session.commands`: total de comandos, incluindo paradas em zero.
- `peak_abs_level`: pico absoluto comandado, preservado após parar.
- `at_limit`: comandos cujo módulo chegou exatamente ao teto configurado.
- `clipped`: comandos ativos cujo pedido do mixer excedeu o teto final.
- `at_limit_ratio`, `clipped_ratio`: proporções 0..1; `null` sem comandos.

Chegar ao teto e excedê-lo são situações distintas. Durante o slew, um pedido
pode ter clipping sem o comando já ter chegado ao teto. Comparar com ganho
global fixo e considerar as duas contagens.

`session_by_spring` separa ×2 (calibração histórica), ×3 (padrão atual), ×4,
transição e outros valores de perfil.
`recent` usa **cinco segundos**, com a mesma separação em `by_spring`.
Transições não são atribuídas aos períodos estáveis em ×2/×3/×4. O histórico tem
limite de 2048 comandos; `truncated=true` informa janela parcial se esse limite
for atingido. Isso não descarta força nem cria fila para reprodução.

Status continua no mailbox existente de 16 KiB; frames/comandos ficam em 4 KiB.
Testes validam status com pool cheio/latch e pacote de força cheio separadamente.
Trace opcional v8 registra seleção, rampa e estatísticas, verificadas no replay,
incluindo detecção de omissão/adulteração. Traces antigos mantêm verificação
de todos os campos originais. Capturas até v7 mantêm os seletores históricos
F5=×2/F6=×3 e os grupos de estatísticas originais; não são reinterpretadas.

## Observação real do operador e comparação ×3/×4

O operador relatou melhor retorno ao centro com ×3 no DiRT 5. Os números
abaixo foram fornecidos pelo operador da última sessão; não constituem uma
captura integral de corrida revisada pelo agente nem medições de torque.

| Spring | Comandos no teto / total | Proporção |
|---|---:|---:|
| ×2 | 4 / 1035 | 0,39% |
| ×3 | 591 / 7229 | 8,18% |
| ×3, últimos cinco segundos | 16 / 146 | 10,96% |

Pico EVDEV informado: 32767; ganho físico comandado: 65535; sem latch durante
a corrida. A comparação temporária passa a ×3/×4, mantendo o perfil salvo em
×3 como padrão e referência de recuperação. Ganhos 0..8 já são aceitos pelo launcher
e pelo serviço; ×4 não exige ampliar a validação. ×4 ainda precisa de
comparação física: pode aumentar o tempo em clipping, sem aumentar o teto.
Comparar F10 nos trechos semelhantes, com ganho global fixo; observar os
grupos estáveis ×3/×4 e a janela recente separadamente das transições.

## Conflitos e validação pendente

- Não havia outro uso de F5/F6 no pacote ativo. F7..F10 e os controles
  aprovados do volante foram preservados.
- Na configuração local `/home/deck/.config/kglobalshortcutsrc` foram
  encontrados **Meta+F5** (`MoveMouseToFocus`) e **Meta+F6**
  (`MoveMouseToCenter`). Os seletores ignoram Ctrl/Shift/Alt/Meta no teclado;
  F9 continua funcionando com modificadores.
- Não foi confirmada associação de F5/F6 do DiRT 5 por fonte primária
  específica ou pelos caminhos locais de configuração consultados. O
  [FAQ genérico da Codemasters](https://codemasters.helpshift.com/hc/en/4-all/faq/370-controller-wheel-setup-instructions-for-pc/?l=en&p=web&s=dirt-5-1600418458)
  menciona F5/F6, mas cita explicitamente F1 2020; não prova conflito no DiRT 5.
  Não se declara ausência de conflito no jogo sem validação real.

O listener readonly não suprime teclas para o jogo ou SteamOS. Se F5/F6 tiverem
outra associação no jogo, essa ação também poderá ocorrer. Não foram alterados
atalhos do sistema, Steam Input ou controles do jogo.

É o mesmo caminho EVDEV de F9/F10, com permissões existentes do serviço
(`DeviceAllow=char-input rw`). Gaming Mode, entrega por teclado físico/Fn,
overlay/Steam Input, possíveis ações do jogo e sensação real da rampa de 300 ms
**ainda exigem validação física pelo operador**. Teclado virtual ou controle
que não produza KEY_F5/KEY_F6 em um dispositivo reconhecido não é equivalente.

## Arquivos e testes

Código em `src/dfgt_bridge/`: `input/global_keys.py`, `supervised.py`,
`physical_worker.py`, `physical_replay.py` e novo `ffb/command_stats.py`.
Testes em `tests/`: `test_global_keys.py`, `test_live_spring.py`,
`test_physical_worker.py`, `test_physical_intensity.py`, `test_physical_replay.py`,
`test_steam_integration.py`, `test_steam_service.py`.
Documentação: este arquivo, README, `SUPERVISED_FFB.md`, `DFGT_CALIBRATION.md`,
`STEAM_INTEGRATION.md` e `DEVELOPMENT.md`.

```sh
python3 -B -m unittest discover -s tests -v
```

Regressões com relógios determinísticos, teclados/soquetes/processos falsos e
MockBackend: seletores, autorepeat, 300 ms, retargeting, ganho do comando versus
cálculo, Stop/Start/Disable/Pause, watchdog, épocas, F9 prioritário, clipping,
outras famílias, perfis/restauração sem escrita, janela limitada, log/F10,
serviço privilegiado fake, instalação temporária e replay v7/v8. Sem dispositivos
reais, pulsos físicos ou instalação no SteamOS executados pelo agente.

Resultado: **574 testes, 573 passaram e um smoke Tk ignorado sem display**,
zero falhas esperadas. A suite completa passou também com ResourceWarning
como erro e audit hook bloqueando dispositivos, bind/unbind/new_id e processos
externos: **zero tentativas**. Referências v0.8/v0.13 mantiveram seus hashes;
nenhum push, publicação ou alteração de hardware/SteamOS foi realizado.
