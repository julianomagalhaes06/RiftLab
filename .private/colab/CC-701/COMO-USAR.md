# CC-701 no Colab

1. Crie no Google Drive a pasta `riftlab-cc701` e envie `riftlab-cc701-colab.zip`.
2. No Colab, use Arquivo → Fazer upload de notebook e abra `CC-701-colab.ipynb`.
3. Selecione GPU T4 em Ambiente de execução → Alterar tipo de ambiente.
4. Execute as células em ordem e autorize a montagem do Drive.
5. A última célula baixa `riftlab-cc701-resultados.zip`; devolva esse arquivo.

Se cair, checkpoints persistem no Drive. Rode de novo para continuar. Se o teste
já tiver começado e não terminado, o runner recusa repeti-lo: execute só a última
célula para gerar o ZIP parcial e devolva para análise. Não apague resultados nem
altere grade/seed. Se GPU não estiver disponível, DEVICE='CPU' é opção explícita
no próprio Colab, com diretório separado; não executar localmente no WSL.

Não enviar bundle-map-private.json. ZIP de treino não contém esse mapa nem IDs
originais, raw, conta ou segredos. Dataset original e tentativas interrompidas
continuam privados locais. Notebook foi validado em sintaxe, integridade e
check-only; treino remoto ainda não executado. Nenhum treino local nesta preparação.
