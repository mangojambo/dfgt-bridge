# Defaults oficiais, perfis e GUI de botões/eixos

Decisão aprovada pelo operador, aplicada em `fix/ffb-core` em 2026-10-25. Não houve execução de motor, instalação ou alteração de drivers/SteamOS nem push durante esta implementação.

## Defaults oficiais

| Opção | Perfil normal |
|---|---|
| `ffb_strength` | **100** |
| `ffb_max_strength` | **100** |
| `ffb_polarity` | **−1**, padrão oficial invertido |
| `ffb_slew_rate` | **327670 unidades/s** |
| `ffb_seconds` | **unlimited** (`null` no JSON) |
| `gas_axis` | **y** |
| `identity` | **g29**, `046d:c24f` |
| `auto_pidff` | **true** |

Os defaults vêm de `config.py`, compartilhado por CLI, GUI e supervisor. Perfis salvos e opções explícitas podem escolher valores diferentes; a intensidade continua editável em 0..100%. Não há retorno automático a 10%/20% nem outro teto inferior ao escolhido.

O modo passivo permanece padrão de execução: **configuração de 100% não autoriza saída física sozinha**. É necessário `--enable-ffb` ou clicar em **Ativar FFB físico**. Abrir a GUI, carregar um perfil, editar ou salvar não cria UHID nem abre FFB. Nenhuma captura é criada por padrão.

`unlimited` remove somente o prazo global da sessão supervisionada. Preserva watchdog independente de cálculo (100 ms), efeito EVDEV finito (160 ms), F9/`ffb-stop`, barreiras de Stop/Reset/Disable, perda do jogo/dispositivo, encerramento e limpeza do slot. O controlador Constant isolado continua exigindo autorização explícita para ilimitado fora do mock; o supervisor fornece essa autorização. `--ffb-seconds 30..240` continua disponível como escolha explícita. F9 não alterna para ligado: exige `ffb-arm` e cálculo novo.

## Auditoria dos botões

O descritor, identidade e funções de montagem do relatório v0.8 não foram modificados. O novo mapper altera apenas quais bits/hats são pedidos, antes de chamar a mesma função `report()`. Índices abaixo são **Button 1..25 do descritor**, não números de código EVDEV. A atribuição nativa dos campos usa a [referência de engenharia reversa G29](https://git.necronda.net/lorenz.stechauner/Device-Driver/raw/commit/a61165f33dab69d1b8d20082e29a083a9b791b20/logitech-G29.md); o layout original continua sendo validado contra a v0.8.

| Entrada DFGT | Destino padrão G29 |
|---|---|
| 288 / 289 / 290 / 291 | X / Quadrado / Círculo / Triângulo: botões 1..4, preservados |
| 293 + 301 | **L1, botão 6**, estado agregado |
| 292 + 300 | **R1, botão 5**, estado agregado |
| 294 / 295 | R2 / L2, botões 7 / 8, preservados |
| 296 / 297 | Share / Options, botões 9 / 10, preservados |
| 298 / 299 | R3 / L3, botões 11 / 12, preservados |
| 303 / 706 | D-pad Up / Down, agregados ao D-pad original |
| ABS_HAT0X / ABS_HAT0Y | D-pad original, incluindo diagonais |
| 707, buzina GT | **+ virtual**, botão 20 |
| 302, clique do dial | **Enter**, botão 24 |
| 704 / 705 | **Rotary Right / Left**, botões 22 / 23; confirmar sentido físico pelo monitor |
| 708, central/Guide | PS, botão 25 |
| 709..712 | Bits legados adicionais preservados nos botões 22..25; origem física não confirmada |

O botão **− virtual**, as saídas do shifter e qualquer outro destino podem receber associações no personalizado. O − físico é D-pad Down por decisão do operador; não é confundido com o botão virtual −.

O código legado `scripts/legacy/dfgt_bridge.py` registra que **300/301 são compartilhados pelo manche e por comandos do corpo**. O mapper não cria identificadores fictícios para diferenciá-los. A ilustração física mostra os mesmos códigos nas duas posições, e ambos são destacados juntos. Não é possível manter estados independentes de dois controles se o driver só informa um mesmo código.

Por outro lado, **códigos distintos** associados ao mesmo destino usam OR: soltar 293 não solta L1 enquanto 301 estiver pressionado. D-pad e botões também agregam fontes; direções opostas simultâneas resultam em eixo de hat neutro. Não se usa ordem de chegada para inventar prioridade.

O padrão não envia borboletas ou manche para X/Quadrado. L1/R1 continuam destinados à navegação de abas; a troca de marchas deve ser configurada dentro do jogo. Isso é comprovado por vetores de bits, mas a navegação efetiva precisa de conferência pelo operador no DiRT 5.

### Dial

Os códigos 704/705 são apresentados como sentidos A/B a confirmar. Não foi feito um teste físico nesta entrega. Para verificar sem motor:

1. Abrir a GUI e clicar **Monitorar DFGT (leitura)**.
2. Clicar **Conferir giro para direita** e girar o dial para a direita.
3. Conferir código/nome detectados e destino. Se estiver invertido, usar **Trocar sentidos A/B do dial**; o clique 302 continua separado.
4. Conferir também o giro para esquerda. Salvar o perfil.

Pulsos de pressão/soltura dentro do mesmo lote continuam detectáveis. A GUI destaca o **último pulso** em amarelo, sem manter a saída pressionada. Verde indica estado atual pressionado; azul indica seleção. A captura de entrada do runner conserva todos os quadros SYN_REPORT, inclusive pressão e soltura separadas.

## GUI

```sh
cd /path/to/dfgt-bridge
python3 -B scripts/run-gui.py
```

Requer Python com Tk. Não foi adicionada dependência de Qt, navegador, `python-evdev`, imagens remotas ou instalação de pacotes. A janela tem as abas **Perfil e FFB** e **Botões e Eixos**, com ilustrações próprias do DFGT e do G29 lado a lado. Os SVGs são CC0-1.0, não contêm logotipos/fotografias de terceiros e incluem hotspots de todos os 25 botões virtuais, D-pad e eixos. Ver [a licença/proveniência dos recursos](../src/dfgt_bridge/assets/README.md).

Selecionar um botão na imagem ou na lista mostra todas as entradas associadas, com código EVDEV, símbolo e nome amigável. É possível remover, digitar um código ou usar **Detectar botão**, adicionar e mover. Mover uma entrada já associada exige uma decisão explícita de conflito. **Ver conflitos / agregações** mostra os códigos compartilhados, associações OR e origens de eixos repetidas. **Restaurar G29 padrão**, **Desfazer** e **Refazer** permitem recuperar a configuração; o histórico é limitado a 50 alterações.

Os eixos podem selecionar ABS_X/Y/Z e inversão, com normalização de ranges na aplicação. O padrão preserva ABS_X 0..16383 e os pedais 0..255, com acelerador HID Y. Nenhum EVIOCSABS ou mudança de range/rotação no dispositivo é feito. A posição usada pela matemática FFB continua sendo a do volante físico, ABS_X; remapear uma entrada virtual não transforma um pedal em sensor mecânico do motor.

A visão em tempo real mostra entradas, destinos e os bytes HID produzidos pelo **mesmo mapper e montador da CLI**. É uma **prévia do perfil em edição**, não leitura do kernel nem medição de força. Mudanças salvas são aplicadas no próximo início do runner; a GUI informa isso e não altera silenciosamente um runner já ativo.

O monitor abre somente o DFGT `046d:c29a` em O_RDONLY após clique explícito. Perda do dispositivo limpa os estados visuais e cancela a detecção. A leitura e os eventos de diagnóstico são limitados por lote; não há fila ilimitada. Se faltarem permissões, a GUI mostra o erro, sem instalar drivers ou modificar ACL/udev.

### Presets

- **G29 padrão:** o mapa aprovado da tabela; L1/R1 em borboletas/manche.
- **Marchas prontas:** por escolha explícita, Quadrado para reduzir e X para aumentar, com destinos configuráveis nas duas listas. Exibe aviso de possíveis conflitos com menu/ações, inclusive depois de salvar/recarregar. Configure a semântica das marchas no jogo.
- **Personalizado:** conserva o mapa atual e permite editar todos os destinos/eixos. Alterar associações produz um perfil personalizado; não troca implicitamente para um preset de marchas.

### Iniciar e parar

**Iniciar passivo** e **Ativar FFB físico** executam o runner em subprocesso somente após clique. Há uma opção explícita para elevar apenas o runner com `pkexec`, quando disponível, e **Copiar comando físico** não executa nada. Não se instala polkit nem se muda configuração do SteamOS. Abrir a GUI sem elevação continua possível para editar offline.

**PARAR FFB / bridge** envia `ffb-stop` e `quit` e fecha o canal. Fechar a GUI também encerra seu runner. O flag interno `--control-stdin` faz o runner encerrar ao receber EOF da GUI, inclusive com elevação, em vez de ficar órfão após perda da interface. Esse flag não é incluído no comando manual copiado para o terminal. O histórico de log visual é limitado a 200 mil caracteres; não gera arquivo de captura.

A GUI inicia o bridge, não o executável DiRT 5. Esperar `[PRONTO]` e abrir o jogo pela Steam, selecionando o G29 virtual. Launchers históricos não foram redirecionados. A supervisão de processo atual continua específica ao DiRT 5; nomes de perfis não implementam autodetecção de outros jogos.

## Perfis compartilhados com a CLI

Por padrão: `$XDG_CONFIG_HOME/dfgt-bridge/profiles.json`, ou `~/.config/dfgt-bridge/profiles.json`. Formato versionado (`version=1`), um perfil por nome de jogo, contendo FFB, preset, mapa e eixos. Escrita atômica, validação antes de substituir, limite de 1 MiB/100 perfis. Arquivo inválido gera erro; não é substituído silenciosamente por defaults. Carregar um perfil novo não cria arquivo até salvar.

Para usar o perfil criado pela GUI, mantendo o caminho do usuário mesmo com sudo:

```sh
sudo /usr/bin/python3 -B scripts/run-passive.py \
  --profiles-file "$HOME/.config/dfgt-bridge/profiles.json" --profile dirt5 --enable-ffb
```

Sem arquivo/perfil existente, esse comando usa 100% / 100%, polaridade −1, slew 327670, duração ilimitada, G29/PIDFF e acelerador Y. Não cria captura. Alternativamente, os valores podem ser explicitados:

```sh
sudo /usr/bin/python3 -B scripts/run-passive.py --enable-ffb \
  --ffb-strength 100 --ffb-max-strength 100 --ffb-polarity -1 \
  --ffb-slew-rate 327670 --ffb-seconds unlimited \
  --identity g29 --auto-pidff --gas-axis y
```

`--profile`, `--profiles-file`, `--preset` e `--save-profile` usam o mesmo `ProfileStore` da GUI. Opções explícitas prevalecem sobre o perfil; sem `--save-profile` não alteram o arquivo. `--save-profile` salva antes de **executar** o runner, não é um modo offline isolado. Os testes de CLI interceptam a execução; editar/salvar na GUI é a opção offline sem abrir dispositivos.

Capturas novas incluem o mapa completo em metadata e guardam a entrada física original, para replay determinístico. Capturas antigas sem mapa continuam usando a semântica legada byte a byte. A política de replay também conserva o slew antigo quando omitido no trace histórico; não aplica defaults novos retroativamente.

## Validação e limites desta entrega

**415 testes passando**, zero falhas esperadas e zero ignorados. Incluem defaults oficiais na ABI EVDEV falsa, operação por mais de 500 s simulados com watchdog, agregação/release, bits L1/R1, hats, controles especiais, eixos, presets, undo/redo, conflitos, detecção de pulso, callbacks da GUI com widgets falsos, perfil compartilhado com CLI, nenhuma execução na abertura da GUI e encerramento por EOF. A suite usou audit hook que proíbe abertura de dispositivos reais.

Os dois pares reais de capturas de 5% e 10% passaram novamente no replay completo/correlacionado, sem modificar seus arquivos. As referências v0.8/v0.13, descritor e launchers foram preservados.

Os SVGs foram renderizados e inspecionados offline. A sessão não conseguiu conectar ao display `:0`, portanto não se afirma validação interativa da janela Tk neste ambiente. Uma tentativa de build de wheel isolada em `/tmp` encontrou `setuptools` ausente; nada foi instalado. O código fonte executa pelos scripts acima, e `pyproject.toml` declara os recursos/entrypoints para empacotamento futuro.

Antes da publicação, o operador deve conferir: janela em resolução real do SteamOS, sentido A/B do dial, códigos compartilhados do corpo, navegação de abas/menu no jogo e início/encerramento físico com o perfil normal. Esses critérios físicos não são declarados concluídos por testes sintéticos. Nenhum comando de motor deve ser executado automaticamente para fazê-los passar.


A integração opcional pela Steam tem configuração própria na aba **Geral** e usa os mesmos perfis. Fechar a GUI não encerra uma sessão pertencente ao launcher Steam. Instalação única, Launch Option e recuperação: [STEAM_INTEGRATION.md](STEAM_INTEGRATION.md).
