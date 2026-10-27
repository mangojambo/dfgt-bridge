# Inicialização automática pela Steam

A integração é coberta por testes offline e foi validada manualmente pelo operador em SteamOS/DiRT 5. Essa validação não equivale a certificação para outros ambientes. Nenhum serviço, módulo ou motor foi iniciado pelo agente durante esta etapa.

## Arquitetura e autorização

O launcher roda como `deck`, mantém os argumentos originais de `%command%` e inicia Steam/Proton/jogo sem privilégios. O serviço systemd executa o runner/backend a partir de uma cópia instalada, pertencente a root, em `/var/lib/dfgt-bridge/runtime`. Alterações no checkout só chegam ao serviço após repetir o instalador.

A comunicação usa socket Unix SOCK_SEQPACKET, permissão 0600 e credenciais SO_PEERCRED. Só o UID instalado pode solicitar `start`, `status`, `ping` e `stop`. O protocolo não aceita executáveis, argumentos arbitrários, caminhos de saída ou ambiente para root. O serviço aceita apenas o perfil `dirt5` aprovado e bloqueia outra sessão simultânea. Steam e seus ancestrais são excluídos da árvore de processos monitorada.

Unidades: `dfgt-bridge.socket` ativa `dfgt-bridge.service`; `dfgt-bridge-modules.service` carrega somente os módulos UHID/PIDFF já instalados. Não instala drivers, não descarrega módulos e não troca o driver do DFGT físico. O backend usa apenas o DFGT `046d:c29a`; G920 é recusado. As permissões do serviço limitam dispositivos e escrita em sysfs. Não há sudoers genérico, alteração de udev, escrita em `/usr` ou desativação do modo somente leitura.

O perfil permanece **100% / máximo 100%, polaridade −1, slew 327670, duração ilimitada, acelerador Y, G29 e auto PIDFF**. Mapeamentos aprovados são preservados; configurações incompatíveis produzem erro, sem redução silenciosa. O runner manual continua passivo por padrão; a opção automática instalada é a autorização explícita para o modo físico nas partidas.

## Instalação única

Feche o jogo e qualquer runner anterior. Execute como `deck`, a partir deste checkout confiável:

```sh
cd /path/to/dfgt-bridge
sudo ./install.sh --test-ready
```

A senha é necessária nesta instalação, não a cada partida. `--test-ready` cria o virtual e verifica prontidão **sem backend físico/motor**, encerrando-o em seguida. Pode omitir essa opção para instalar sem iniciar dispositivos. O instalador verifica dependências, drivers disponíveis, usuário, perfil e permissões; aborta diante de caminhos inseguros. Não altera automaticamente Steam ou o jogo.

Instalações repetidas atualizam a cópia protegida e preservam perfis e a preferência de automação já escolhida. A instalação recusa atualização enquanto há sessão ativa. O estado persistente usa `/etc/systemd/system`, `/var/lib/dfgt-bridge` e `/var/log/dfgt-bridge`, sem modificar a imagem de `/usr`.

Cole exatamente nas **Opções de inicialização** do DiRT 5, substituindo o launcher antigo:

```text
PROTON_ENABLE_HIDRAW=0x046d/0xc24f "$HOME/.local/bin/dfgt-steam-launch" -- %command%
```

O wrapper usa argumentos separados, sem `eval`. Não coloque o jogo sob sudo. A GUI não precisa permanecer aberta. Em **Geral → Iniciar automaticamente com o jogo**, a preferência afeta a próxima partida. Desativá-la é uma escolha explícita para abrir o jogo sem bridge; uma falha de inicialização com a opção ativa nunca abre o jogo silenciosamente.

## Prontidão e encerramento

Antes de executar o comando Steam, o launcher exige confirmação do runner: G29 próprio associado a `hid-universal-pidff`, EVDEV virtual existente, capacidades FFB e confirmação do worker físico na sessão normal. Também aguarda acesso de leitura/escrita do usuário ao EVDEV. Falhas apresentam diagnóstico; não há espera infinita em `unbound`.

Os prazos de inicialização não limitam a partida: serviço 40 s, cliente 70 s, aparecimento do jogo 180 s (ajustável com `--game-start-timeout`). Após iniciado, o perfil não tem timer de sessão. O launcher acompanha o processo real DiRT 5 e auxiliares por UID, parentesco, identidade PID/início e Steam AppID 1038250, incluindo handoff do Proton.

Ao terminar o jogo, o launcher solicita parada do FFB antes de aguardar auxiliares. Sinais/cancelamento encerram os processos acompanhados. EOF do cliente, perda da identidade do launcher ou ausência de heartbeat por 3 s encerram a sessão. O serviço verifica progresso do renderer (1,5 s), além do watchdog físico independente existente (100 ms), efeito EVDEV finito (160 ms) e watchdog systemd (5 s). Esses prazos cobrem falhas diferentes, não são uma promessa de latência única.

F7/F8 continuam ajustando intensidade, F9 trava a saída em zero e F10 mostra diagnóstico. Após F9, reiniciar explicitamente a sessão é a forma de reativar no modo automático. Se teclas globais não estiverem disponíveis, use o comando independente `--stop` abaixo. SIGKILL do launcher fecha sua conexão e interrompe o FFB; ele não pode executar cleanup Python dos processos Wine nessa situação. Use **Parar** na Steam para auxiliares remanescentes.

Na versão v1.1.0, F5 seleciona Spring ×3 e F6 seleciona ×4,
interpolando em 300 ms diretamente no worker físico. Basta reinstalar por
`sudo ./install.sh`; a Launch Option permanece igual. A escolha é temporária,
preserva perfis personalizados e não rearma F9. F10 inclui ganho aplicado,
comando final, pico e estatísticas de saturação. Funcionamento em Gaming Mode
e conflitos de atalhos ainda requerem confirmação real; detalhes em
[LIVE_SPRING.md](LIVE_SPRING.md).

## Validação no equipamento

1. Rode o teste passivo da instalação: deve mostrar `[PRONTO]` com PIDFF, EVDEV e capacidades. Ele não testa torque.
2. No Desktop Mode, inicie pela Steam com a Launch Option acima. A próxima partida usa o perfil físico aprovado de 100%; mantenha F9 disponível. Confira enumeração G29, direção, acelerador, freio, botões e L1/R1 das borboletas/manche.
3. Feche normalmente, confirme sessão encerrada com `--status`, abra novamente e teste **Parar** pela Steam. Verifique ausência de segunda instância e limpeza do efeito.
4. Repita em Gaming Mode sem GUI ou terminal aberto. Valide F9/F10 e recuperação após cancelar a inicialização. Não considere os testes offline como validação dessas etapas.

O estado também pode ser consultado manualmente pela aba Geral. Fechar a GUI não encerra uma sessão pertencente à Steam.

## Logs, captura e recuperação

Sem captura detalhada por padrão. Logs operacionais: `/var/log/dfgt-bridge/service.log` (256 KiB + 2 backups), `~/.local/state/dfgt-bridge/steam.log` (128 KiB + 1 backup), além do journal com limitação por unidade. Erros do launcher também tentam notificação de desktop, sem depender dela para abortar.

Para uma coleta explicitamente solicitada, acrescente `--capture` antes de `-- %command%`. O serviço escolhe nomes únicos em `/var/log/dfgt-bridge/captures/`; esses arquivos não têm rotação automática e precisam de espaço livre. O replay continua offline pelo mesmo núcleo, sem backend físico.

Comandos como usuário:

```sh
~/.local/bin/dfgt-steam-launch --status
~/.local/bin/dfgt-steam-launch --stop
```

Em falha persistente, feche o jogo e consulte:

```sh
sudo journalctl -u dfgt-bridge.service -u dfgt-bridge-modules.service -n 100 --no-pager
sudo systemctl status dfgt-bridge.socket dfgt-bridge.service dfgt-bridge-modules.service
```

Para recuperar uma unidade em falha após corrigir a causa:

```sh
sudo systemctl stop dfgt-bridge.service
sudo systemctl reset-failed dfgt-bridge.service dfgt-bridge-modules.service
sudo systemctl restart dfgt-bridge-modules.service dfgt-bridge.socket
```

Não descarregue drivers Logitech nem faça bind de dispositivos físicos. Se uma atualização do sistema invalidar componentes/permissões, repita o instalador e o teste passivo. Não se afirma compatibilidade já testada com atualizações A/B do SteamOS.

Desinstalação, com jogo fechado:

```sh
cd /path/to/dfgt-bridge
sudo ./uninstall.sh
```

Remova manualmente a Launch Option na Steam. O desinstalador é repetível, desativa/remove as unidades próprias e a cópia instalada, preservando perfis, logs e capturas. Não descarrega módulos usados por outros dispositivos.

## Evidências e limites

459 testes passam, sem testes ignorados ou falhas esperadas. Processos, dispositivos, credenciais e systemd são simulados; a suíte usa bloqueio adicional contra abertura de dispositivos reais. Cobrem autorização, prontidão, falha PIDFF, argumentos/ambiente, lifecycle, duas instâncias, reinício, perda de progresso, limpeza, instalação repetida e sessão ilimitada.

O sandbox bloqueou operações de socket Unix e a verificação nativa de unidades systemd; portanto IPC real e endurecimento das unidades ainda exigem validação no alvo. O teste de sintaxe shell e o plano `./install.sh --dry-run` passaram. Exclusividade por flock é cooperativa: outro programa que ignore locks ainda pode escrever FFB. Não execute simultaneamente launchers experimentais. Não houve instalação real, alteração do SteamOS ou push.

Referências de implementação: [systemd.socket](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html), [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html), [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html), [credenciais de sockets Unix](https://man7.org/linux/man-pages/man7/unix.7.html) e [AppID do DiRT 5](https://store.steampowered.com/app/1038250/DIRT_5/).

## Correção do erro 13 após a primeira ativação (2026-10-25)

A auditoria encontrou colisão de propriedade entre `RuntimeDirectory=dfgt-bridge`
(root) e `/run/dfgt-bridge/control.sock` (usuário, 0600). Ao corrigir o dono do
diretório, systemd também pode mudar recursivamente o dono do socket já criado.
A primeira conexão ativa o serviço; conexões posteriores falham em
`connect(/run/dfgt-bridge/control.sock)` com EACCES. Esse mecanismo está descrito
em [RuntimeDirectory no systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectory=).

As evidências locais disponíveis mostram prontidão passiva seguida por erro 13 no
launcher, sem nova sessão no log do serviço; o socket possui o mesmo dono dos
arquivos root, em vez do UID 1000 configurado. Isso sustenta a causa acima; o log
antigo não contém traceback nem registra a syscall, portanto não prova sozinho
qual operação falhou na execução original. A nova instrumentação identifica
`launcher.control.connect`, leitura do perfil, criação da sessão, escrita privada,
socketpair, spawn e falhas I/O do runner, com errno e caminho. Caminhos privados
em home são abreviados; conteúdo do perfil e ambiente não são registrados.

O backend agora usa **`/run/dfgt-bridge-backend` (0700)** para lock e sessões.
O socket continua em `/run/dfgt-bridge/control.sock`, 0600 para o usuário instalado,
fora da árvore gerida pelo serviço. A atualização verifica também o lock legado
antes de parar a instalação anterior. Não se relaxam permissões do socket.

Leitores do perfil: GUI/CLI usam `ProfileStore`; o launcher lê o JSON 0600 no home
como usuário e valida o perfil aprovado. O instalador abandona privilégios antes
de acessar arquivos do usuário. O daemon recebe apenas valores pelo IPC com
SO_PEERCRED, revalida-os e escreve sua cópia 0600 na sessão privada em `/run`.
O runner privilegiado lê essa cópia explícita, sem precisar acessar `/home`.
O protocolo continua recusando caminhos, argv, ambiente e comandos arbitrários.

`ProtectHome=yes` e `ProtectSystem=strict` permanecem. Escritas de execução ficam
no `RuntimeDirectory`; logs e captura opt-in ficam no `LogsDirectory`. O snapshot
e a política em `/var/lib` são somente leitura durante execução e só o instalador
os atualiza. Não há necessidade de tornar `/home` ou `/var/lib` graváveis no daemon.

Com jogo fechado, atualize a instalação existente a partir deste checkout:

```sh
cd /path/to/dfgt-bridge
sudo ./install.sh
~/.local/bin/dfgt-steam-launch --status
~/.local/bin/dfgt-steam-launch --check-ready
~/.local/bin/dfgt-steam-launch --status
~/.local/bin/dfgt-steam-launch --check-ready
~/.local/bin/dfgt-steam-launch --status
stat -c '%u %g %a %n' /run/dfgt-bridge/control.sock
```

A reinstalação para as unidades antigas e recria o socket; não basta copiar o
Python ou reiniciar somente o daemon. Os dois probes devem imprimir `[PRONTO]`,
sem motor/jogo. O status pode mostrar `stopping` brevemente após o probe; aguarde
`idle` antes de repetir. O socket deve continuar UID 1000 e modo 600 depois da
ativação (substitua 1000 pelo UID instalado se diferente). Só então abra o DiRT 5.
Se falhar, consulte `steam.log` e o journal conforme a seção anterior; a mensagem
nova informa operação/caminho/errno. Não use chmod amplo nem desative o sandbox.

USB: a entrada é reaberta pelo link estável `/dev/input/by-id/`, com validação
046d:c29a; não há eventN físico fixo. A desconexão termina a sessão e zera/encerra
sua saída pelo cleanup/watchdog existente. Não há rearmamento automático ao
reconectar. Com jogo fechado, reconecte, aguarde o link by-id e `idle`, e repita
`--check-ready`; uma nova sessão resolve o novo eventN e descarta o estado antigo.

Regressões offline incluem perfil 0600, home inacessível simulado, separação das
árvores systemd, erros de permissão, limpeza após falha, reconexão com novo fd,
locks das duas instalações e partidas repetidas. O comportamento systemd e USB
real ainda requer o procedimento passivo no SteamOS; nenhum dispositivo foi
aberto nem serviço alterado pelo agente nesta correção.
