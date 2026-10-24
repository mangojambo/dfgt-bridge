# Associação e prontidão do G29 virtual

## Causa da regressão

A v0.8, com `--identity g29 --auto-pidff --gas-axis y`, executava `modprobe hid-universal-pidff`, identificava o novo HID em `/sys/devices/virtual/misc/uhid/` e registrava `0003 0000046D 0000C24F` em `hid-universal-pidff/new_id`. A escrita ocorria em uma thread enquanto o loop principal respondia GET_REPORT/SET_REPORT. Esse atendimento é necessário porque o probe pode aguardar respostas sincronicamente; consulte o [contrato UHID](https://docs.kernel.org/hid/uhid.html).

O runner refatorado conservou a criação UHID, mas omitiu essa preparação. Apenas consultava o driver e continuava mesmo sem EVDEV ou FF. Assim, a seleção inicial de `logitech`, seguida de probe `-22`, deixava o G29 sem driver e sem entrada para o jogo. As capturas anteriores com PIDFF demonstram que havia associação disponível naquelas sessões; não demonstram que o runner a criava. Uma regra dinâmica deixada por uma execução anterior é uma explicação possível, não uma reinicialização comprovada.

O descritor funcional, os bytes dos controles e as referências v0.8/v0.13 não foram modificados por esta correção.

## Associação implementada

`pid_binding.py` é separado do transporte PID e não abre dispositivos. Antes da criação UHID, verifica o driver e carrega **somente o módulo já instalado**, se necessário, com prazo de cinco segundos. Não instala pacotes nem escreve configuração persistente. No ambiente inspecionado, o módulo existe em `/lib/modules/7.2.7-valve2-2-neptune-72-g31504582a073/kernel/drivers/hid/hid-universal-pidff.ko.zst`, mas não estava registrado em sysfs. Essa inspeção foi somente leitura: nenhum carregamento ou bind real foi executado durante o desenvolvimento.

Cada execução coloca um token de propriedade no campo PHYS do CREATE2. Só aceita o nó novo com esse token, nome e VID/PID esperados, diretamente abaixo do caminho UHID virtual. UNIQ/serial continua vazio e o descritor não muda. Isso evita introduzir um serial aleatório na identificação usada pelo [Wine winebus](https://github.com/wine-mirror/wine/blob/master/dlls/winebus.sys/bus_udev.c). O CREATE2 padrão da API, sem PHYS informado, continua idêntico ao da v0.8.

A associação tenta primeiro `bind` do nó exato, aproveitando uma regra existente. Se houver outro driver, somente o nó virtual comprovadamente pertencente ao processo pode receber `unbind`. DFGT `c29a`, G920 `c262` e outros Logitech não são alvos. ENODEV durante a remoção de um probe Logitech já falho é tratado como uma corrida de remoção; outros erros são exibidos.

Se o bind não encontrar regra compatível (ENODEV), o fallback registra o mesmo ID usado pela v0.8, **somente se o único G29 presente for o nó próprio**. Outro G29 real ou virtual bloqueia o registro, inclusive numa segunda verificação imediatamente antes da escrita. Se o driver estiver ausente e já houver outro G29, o runner também recusa seu carregamento.

### Limitação da API HID

O [HID core Linux](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-core.c) registra `new_id` por barramento/VID/PID e chama `driver_attach()` globalmente. Não oferece `driver_override` por dispositivo nem `remove_id` nessa interface. O [bind do driver core](https://github.com/torvalds/linux/blob/master/drivers/base/bus.c) exige correspondência na tabela de IDs; escrever somente o nome do nó não contorna isso.

Portanto, o fallback **não é uma regra por instância**: vale para qualquer G29 USB `046d:c24f` e permanece até descarregar o módulo ou reiniciar. O runner informa essa limitação quando registra a regra. Recusa outros G29 já presentes, mas não pode garantir isolamento de um G29 físico conectado posteriormente. Não conectar outro G29 durante essa sessão. Não há remoção automática do módulo, pois isso poderia afetar outros dispositivos. A regra não corresponde ao DFGT ou G920. Nenhum arquivo SteamOS/udev/Steam, parâmetro global HID ou driver físico é alterado pelo bridge.

## Critério de prontidão e falhas

O runner só imprime `[PRONTO]` quando a escrita de associação terminou e o mesmo nó possui:

- Driver `hid-universal-pidff`.
- Um `event*` descendente do próprio HID, com dispositivo caractere existente em `/dev/input` e major/minor correspondentes a sysfs.
- EV_ABS, ABS_X, EV_FF e FF_CONSTANT nessa mesma entrada.

A verificação usa `stat` e sysfs, sem abrir EVDEV virtual. O prazo padrão é 25 segundos (`--bind-timeout 1..60`). Driver ausente, probe com erro, ausência de entrada/capacidades, timeout e perda posterior do nó geram erro com fase, driver, caminho, entradas e capacidades. Falhas não viram espera indefinida em `unbound`. Mensagens sugerem conferir o log do kernel para o nó; não inventam uma leitura do erro `-22`.

O loop continua respondendo GET/SET/OUTPUT enquanto a thread executa bind/new_id. UHID_STOP durante a troca inicial de driver limpa efeitos/caches e permite continuar; após a prontidão, volta a encerrar a sessão. Essas fases são ações explícitas `binding` na captura, reproduzidas offline. O último relatório completo de controles é reenviado após a associação, pois o primeiro pode ter sido enviado antes de existir um leitor. Esse reenvio não cria amostra nem derivadas de movimento.

Mesmo com `--enable-ffb`, nenhum processo de saída física inicia antes dessas verificações e de nova checagem após o reenvio. Cancelamento encerra a tentativa antes de destruir UHID; o join da thread ocorre depois de fechar UHID para liberar uma resposta de probe pendente. O modo passivo continua sem writer físico. Perda do nó após ficar pronto encerra o runner e seus recursos.

## Validação e próximo teste

346 testes passam, sem falhas esperadas ou ignorados, incluindo sysfs/dispositivos falsos, associação, probe Logitech falho, módulo ausente, timeout, ACK durante bind bloqueado, cancelamento, desconexão, EVDEV/FF inválidos e preservação dos dispositivos físicos. A suite executou com bloqueio de abertura de dispositivos. A enumeração real ainda precisa ser confirmada no SteamOS; esses testes não provam reconhecimento pelo jogo. As três capturas de referência passaram novamente em replay com `--verify-compatible`: 35.723, 37.280 e 54.648 ações, sem divergência semântica ou erro de entrega. Os 36 arquivos históricos conferidos pelo manifesto e as três capturas mantiveram seus hashes.

Iniciar primeiro em modo passivo, sem outra instância do bridge:

```sh
cd /home/deck/dfgt-bridge
sudo /usr/bin/python3 -B scripts/run-passive.py \
  --identity g29 --auto-pidff --gas-axis y \
  --capture captures/dirt5-pidff-01.jsonl.gz
```

`--auto-pidff` é aceito por compatibilidade e agora é o padrão. Esperar `[PRONTO]` com driver PIDFF, EVDEV e capacidades antes de abrir o jogo. Conferir G29, controles e menu FFB. Não adicionar `--enable-ffb` para esse diagnóstico de reconhecimento. Se o arquivo já existir, escolher o nome alternativo sugerido, preservando a captura.

Depois do encerramento:

```sh
python3 -B scripts/replay-capture.py captures/dirt5-pidff-01.jsonl.gz
python3 -B -W error::ResourceWarning scripts/test-offline.py
```

Replay não carrega módulos nem associa drivers: só reproduz os eventos e ações capturados no núcleo mock. Os resultados e respostas devem coincidir, não apenas o parser terminar sem exceção.
