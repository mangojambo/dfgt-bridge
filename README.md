# DFGT Bridge

Bridge experimental para apresentar um Logitech Driving Force GT (`046d:c29a`) como volante G29 virtual (`046d:c24f`) no SteamOS/DiRT 5. Os controles da v0.8 são a baseline funcional; o FFB está em desenvolvimento incremental e ainda não foi validado fisicamente nesta fase.

## Organização

```text
AGENTS.md / README.md / pyproject.toml / .gitignore
src/dfgt_bridge/       entrada readonly, runner passivo, PID, matemática, captura/replay e mock
reference/v0.8/        baseline funcional preservada
reference/v0.13/       experimento auditado preservado, sem correções retroativas
archive/experiments/   versões anteriores por família e versão
scripts/              launchers, ferramentas e bridge Xbox legado
tests/fixtures/dirt5/  cinco CSVs pequenos originais, 132 registros
captures/             capturas locais e logs, ignorados pelo Git
docs/                 auditoria, manifesto, mapa de movimentos e desenvolvimento
```

A entrada e o runner passivo foram implementados sem alterar as referências. Não há backend físico nem módulos vazios antecipando abstrações futuras.

## Testes e execução segura

Só é necessário Python 3.10+ para os testes offline; não instale `evdev` para executá-los.

```sh
python3 -B -m unittest discover -s tests -v
```

A reorganização conservou inicialmente **18 testes: 5 passando e 13 falhas esperadas**. O estado atual é **220 testes passando, nenhuma falha esperada e nenhum teste ignorado**. As 13 divergências iniciais foram corrigidas e seus casos convertidos em testes normais. Veja [o estado de desenvolvimento](docs/DEVELOPMENT.md), [as fórmulas](docs/FFB_MATH.md) e [o contrato de transporte/ciclo de vida](docs/PID_TRANSPORT.md).

Os scripts históricos exigem outras dependências e podem acessar hardware ou drivers. Não são executados pela suite offline. O novo pacote não substitui automaticamente o comando configurado no Steam. `scripts/run-passive.py` cria o G29 virtual com controles da v0.8 e calcula FFB exclusivamente no mock. `scripts/replay-capture.py` valida as capturas offline. Os entrypoints inserem `src/` explicitamente porque o nome legado `dfgt_bridge.py` na raiz colide com o nome do pacote.

Comandos para execução passiva, captura completa, marcação de fases e replay estão em [PASSIVE_RUNNER.md](docs/PASSIVE_RUNNER.md). Nenhum runner foi iniciado em dispositivos nesta entrega. Para repetir os testes com bloqueio adicional de abertura real de dispositivos: `python3 -B -W error::ResourceWarning scripts/test-offline.py`.

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
- [Regras para agentes e colaboradores](AGENTS.md)

Testes físicos, alteração de drivers e avaliação de comandos proprietários Logitech dependem de aprovação separada.
