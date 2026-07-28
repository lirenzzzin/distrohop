# Partição-cofre Linux

A partição-cofre é um último recurso para manter uma cópia no mesmo computador.
Ela não sobrevive a “apagar o disco inteiro”: o instalador da próxima
distribuição precisa estar em modo de particionamento manual, e a partição não
pode ser marcada para formatação.

## Portões

Antes de qualquer probe, CLI e GUI mostram o aviso completo e exigem a frase:

```text
EU ENTENDO E VOU USAR PARTICIONAMENTO MANUAL
```

O planejador então exige:

- Linux, dispositivo de bloco e tabela GPT já existente;
- um bundle Distrohop com checksums válidos em outro disco;
- espaço contíguo no final do disco para o tamanho pedido mais 20%;
- ou, quando falta espaço GPT, a última partição em Btrfs montado
  leitura/escrita, single-device e com espaço livre real de pelo menos 120%;
- nenhum balance, scrub ou processo `btrfs subvolume snapshot`/`btrfs send`.

Ext4, XFS, Btrfs multidispositivo, layouts sobrepostos e qualquer probe
inconclusivo são recusados. O caminho seguro nesses casos é live USB/GParted.

## Execução

```sh
./bin/distrohop vault create \
  --disk /dev/sdX \
  --size-gib 32 \
  --backup /mnt/externo/distrohop-meu-pc-... \
  --confirm 'EU ENTENDO E VOU USAR PARTICIONAMENTO MANUAL'

sudo ./bin/distrohop vault create \
  --disk /dev/sdX \
  --size-gib 32 \
  --backup /mnt/externo/distrohop-meu-pc-... \
  --confirm 'EU ENTENDO E VOU USAR PARTICIONAMENTO MANUAL' \
  --execute
```

Sem `--execute`, o comando é sempre dry-run e mostra inclusive o `stdin`
planejado para `sfdisk`. Na execução, o motor revalida o bundle, o disco, a
tabela e o estado Btrfs antes do primeiro write. Ele salva um dump da tabela ao
lado da segunda cópia.

Quando há redução, a ordem é Btrfs primeiro, tamanho da mesma entrada GPT
depois. A nova entrada é adicionada por número explícito e lock, sem
`--reorder`. Antes de `mkfs.ext4`, todas as entradas antigas e a nova são
comparadas byte/setor com o plano. Só então o dispositivo novo é formatado,
montado temporariamente, recebe o README e uma cópia do bundle verificada
novamente. Não existe comando para GRUB, EFI, `fstab` ou reordenação.

O nome GPT é `DISTROHOP-DO-NOT-FORMAT`. O campo de label do ext4 comporta no
máximo 16 caracteres, então recebe `DISTROHOP-DO-NOT`; o nome completo fica no
GPT e no README da raiz.

## Validação segura

Os testes de política cobrem cada aborto isoladamente. O comando real do
`sfdisk` é exercitado somente contra um arquivo de imagem descartável de 64
MiB; nenhum teste recebe `/dev/sdX`, NVMe ou outro disco físico.

Fontes primárias:

- [sfdisk(8)](https://man7.org/linux/man-pages/man8/sfdisk.8.html)
- [btrfs filesystem resize](https://btrfs.readthedocs.io/en/stable/btrfs-filesystem.html)
- [btrfs balance](https://btrfs.readthedocs.io/en/latest/btrfs-balance.html)
- [btrfs scrub](https://btrfs.readthedocs.io/en/latest/btrfs-scrub.html)
- [e2label e o limite de 16 caracteres](https://man7.org/linux/man-pages/man8/e2label.8.html)
