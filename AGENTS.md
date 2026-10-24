# Desenvolvimento do DFGT Bridge

- Leia `README.md`, `docs/FFB_AUDIT.md` e `docs/DEVELOPMENT.md` antes de alterar FFB.
- `reference/v0.8/` é a referência funcional de UHID, controles e reconhecimento do FFB. Preserve seus bytes.
- `reference/v0.13/` preserva o experimento auditado. Não o apresente como corrigido; correções pertencem a `src/dfgt_bridge/`.
- Alterações no descritor UHID exigem regressões de descritor e relatórios de entrada contra v0.8. Separe mudanças de entrada, protocolo PID e saída de força em commits distintos.
- Testes automatizados não podem acessar o motor físico. Nunca abrir `/dev/input`, `/dev/uhid`, hidraw ou USB durante testes offline; não executar uploads EVDEV FF, comandos Logitech proprietários, bind/unbind, modprobe ou alterações de drivers.
- Use backend simulado por padrão. Testes devem usar relógio determinístico e dispositivos falsos. Não importe ou execute os entrypoints dos scripts de referência/arquivo para testar classes.
- A suite inicial possui 18 testes e 13 `expectedFailure` documentados. Corrija a implementação antes de converter um caso resolvido em teste normal. Não remova asserts, não ignore testes e não amplie tolerâncias para esconder falhas.
- Preserve as fixtures reais e sua proveniência. Rotule vetores sintéticos; não descreva o corpus atual como validação de efeitos completos de corrida.
- Prefira commits pequenos, com testes correspondentes, em `fix/ffb-core`. Evite módulos vazios e extrações amplas sem necessidade.
- Não apague arquivos nesta etapa. Consulte `docs/REORGANIZATION.json` antes de mover arquivos ou quebrar caminhos legados. Audite referências antes de novas movimentações.
- Não execute ações destrutivas, testes físicos, comandos USB proprietários, mudanças no SteamOS/Steam/udev/drivers ou substituição do backend físico sem aprovação explícita. Não faça push nem crie remoto sem aprovação.
- Não versione credenciais, logs, caches, binários e capturas grandes. Fixtures pequenas e revisadas ficam em `tests/fixtures/`.
- Neste ambiente `.git/` está montado somente leitura. Use `scripts/git-project.sh` para o repositório local em `.git-local/`; não altere a montagem protegida nem configurações Git globais.

Validação offline: `python3 -B -m unittest discover -s tests -v`.
