# DFGT Bridge

Bridge experimental para apresentar um Logitech Driving Force GT (`046d:c29a`) como volante G29 virtual (`046d:c24f`) no SteamOS/DiRT 5. Os controles da v0.8 são a baseline funcional; o FFB está em desenvolvimento incremental. O operador testou 5% e 10%; a resposta física na faixa completa ainda precisa ser confirmada.

## Organização

```text
AGENTS.md / README.md / pyproject.toml / .gitignore
src/dfgt_bridge/       entrada readonly, runner, PID, matemática, captura/replay, mock e EVDEV opt-in
reference/v0.8/        baseline funcional preservada
reference/v0.13/       experimento auditado preservado, sem correções retroativas
archive/experiments/   versões anteriores por família e versão
scripts/              launchers, ferramentas e bridge Xbox legado
tests/fixtures/dirt5/  cinco CSVs pequenos originais, 132 registros
captures/             capturas locais e logs, ignorados pelo Git
docs/                 auditoria, manifesto, mapa de movimentos e desenvolvimento
```

A entrada e o runner foram implementados sem alterar as referências. O modo passivo continua padrão; o backend EVDEV supervisionado exige `--enable-ffb` e já teve ensaios de baixa intensidade relatados pelo operador. A faixa completa e o diagnóstico estão em [FFB_INTENSITY.md](docs/FFB_INTENSITY.md). Não há módulos vazios antecipando abstrações futuras.

## Testes e execução segura

Só é necessário Python 3.10+ para os testes offline; não instale `evdev` para executá-los.

```sh
python3 -B -m unittest discover -s tests -v
```

A reorganização conservou inicialmente **18 testes: 5 passando e 13 falhas esperadas**. O estado atual é **415 testes passando, nenhuma falha esperada e nenhum teste ignorado**. As 13 divergências iniciais foram corrigidas e seus casos convertidos em testes normais. Veja [o estado de desenvolvimento](docs/DEVELOPMENT.md), [as fórmulas](docs/FFB_MATH.md) e [o contrato de transporte/ciclo de vida](docs/PID_TRANSPORT.md).

Os scripts históricos exigem outras dependências e podem acessar hardware ou drivers. Não são executados pela suite offline. O novo pacote não substitui automaticamente o comando configurado no Steam. `scripts/run-passive.py` cria o G29 virtual com controles da v0.8 e, por padrão, calcula FFB exclusivamente no mock. A saída física opcional usa outro processo e os defaults aprovados: **100% / máximo 100%, polaridade −1, slew 327670, duração ilimitada**. Watchdog de cálculo, efeito EVDEV finito, supervisão do jogo e F7/F8/F9/F10 continuam ativos. `scripts/replay-capture.py` valida capturas offline e o trace físico pareado, sempre em mock. Os entrypoints inserem `src/` explicitamente porque o nome legado `dfgt_bridge.py` na raiz colide com o nome do pacote.

Comandos para execução passiva, captura completa, marcação de fases e replay estão em [PASSIVE_RUNNER.md](docs/PASSIVE_RUNNER.md). Nenhum runner foi iniciado em dispositivos nesta entrega. Para repetir os testes com bloqueio adicional de abertura real de dispositivos: `python3 -B -W error::ResourceWarning scripts/test-offline.py`.

As [duas capturas reais fornecidas pelo operador](docs/CAPTURE_COMPARISON_20261024.md) passaram no replay offline. O CLI agora usa acelerador em **Y por padrão**, reproduzindo a configuração funcional v0.8 (`--gas-axis y`); o comando anterior sem essa opção enviava o pedal em Z e deixava Y em 255. O descritor e a matemática FFB permanecem intactos.

O operador confirmou o acelerador corrigido. A terceira captura, `dirt5-y-20261024-144639.jsonl.gz`, também passou em replay completo: 20.501 quadros, HID Y 0..255 e zero diferenças entre ABS_Y e HID Y. Os comandos de modo físico, parada independente e procedimento manual de polaridade estão em [SUPERVISED_FFB.md](docs/SUPERVISED_FFB.md). O operador posteriormente relatou força fraca a 5% e 10%; a auditoria e a configuração explícita de 100% estão em [FFB_INTENSITY.md](docs/FFB_INTENSITY.md). Nenhum teste de motor foi executado automaticamente.

A associação automática omitida na refatoração foi restaurada: o runner carrega o PIDFF já instalado quando necessário e só informa `[PRONTO]` após confirmar driver, entrada EVDEV virtual e FF_CONSTANT. Antes disso, a saída física não pode iniciar. A propriedade do nó é conferida por token PHYS; descritor, serial vazio e controles permanecem preservados. O fallback `new_id` é global ao VID/PID e é recusado se houver outro G29. Leia [o diagnóstico e as limitações da associação](docs/PID_BINDING.md) antes do próximo teste passivo.

## GUI e perfis oficiais

```sh
python3 -B scripts/run-gui.py
```

A aba **Botões e Eixos** tem vistas próprias/licenciadas do DFGT e G29, seleção visual, detecção readonly, prévia HID, presets por jogo, conflitos, restauração e desfazer/refazer. Abrir a GUI não ativa motor nem cria captura. Borboletas 293/292 e manche 301/300 enviam L1/R1 por padrão; +/− físicos enviam D-pad Up/Down. Perfis usam o mesmo `config.py`/JSON da CLI. Início físico continua explícito, por botão ou `--enable-ffb`. Veja [defaults, auditoria de botões e comandos](docs/PROFILES_AND_MAPPING.md). O giro do dial e a janela interativa ainda precisam de conferência no SteamOS; os widgets foram testados com simuladores.

## Compatibilidade com caminhos existentes

Links relativos na raiz preservam os nomes de `launch-dirt5.sh`, `test-180.sh`, `dfgt_bridge.py`, v0.8 e v0.13. O arquivo `shifter.json` permanece na raiz porque experimentos legados usam `Path.home()/dfgt-bridge/shifter.json`. O log anterior foi preservado em `captures/legacy/`, com link local no caminho antigo; log e esse link ficam fora do Git. Esses launchers continuam apontando para o bridge Xbox legado; não foram redirecionados ao novo FFB.

Os conteúdos movidos foram conferidos por SHA-256. Consulte [o mapa completo](docs/REORGANIZATION.json) e [a análise de caminhos](docs/DEVELOPMENT.md). O manifesto da auditoria original continua sendo um registro histórico, com os nomes e hashes originais.

## Git local

O ambiente disponibiliza `.git/` como uma montagem vazia somente leitura. O repositório desta sessão usa `.git-local/`, dentro do projeto, sem alterar essa montagem. Os comandos abaixo usam o wrapper:

```sh
scripts/git-project.sh status
scripts/git-project.sh log --oneline --decorate
scripts/git-project.sh show baseline-pre-refactor:dfgt_uhid_g29_probe_v08_driving.py
```

Fora desta restrição, um clone convencional usa `.git/` normalmente; o wrapper aceita ambos. Os metadados locais não entram nos commits. A identidade Git é local ao projeto (`DFGT Bridge contributors <dfgt-bridge@localhost>`); nenhuma configuração global foi alterada. Não há remoto configurado.

## Documentação

- [Auditoria técnica de FFB](docs/FFB_AUDIT.md)
- [Manifesto original](docs/FFB_AUDIT_MANIFEST.json)
- [Plano, caminhos e estado atual](docs/DEVELOPMENT.md)
- [Matemática PID, sinais, mistura e aproximações](docs/FFB_MATH.md)
- [Transporte, pool, timeline e scheduler offline](docs/PID_TRANSPORT.md)
- [Plano de integração com a v0.8 e estado da etapa passiva](docs/INTEGRATION_PLAN.md)
- [Runner passivo, comandos e contrato de captura/replay](docs/PASSIVE_RUNNER.md)
- [Associação PIDFF, prontidão e diagnóstico da regressão](docs/PID_BINDING.md)
- [Comparação das capturas reais e correção do acelerador](docs/CAPTURE_COMPARISON_20261024.md)
- [FFB EVDEV supervisionado, controles, replay e primeiro ensaio manual](docs/SUPERVISED_FFB.md)
- [Intensidade 0–100%, perdas, ganho EVDEV e comando de faixa completa](docs/FFB_INTENSITY.md)
- [Defaults oficiais, GUI, auditoria de botões e perfis por jogo](docs/PROFILES_AND_MAPPING.md)
- [Regras para agentes e colaboradores](AGENTS.md)

Testes de motor, alterações de drivers físicos, instalação/configuração persistente e avaliação de comandos proprietários Logitech dependem de aprovação separada.
