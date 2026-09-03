#!/usr/bin/env python
"""
Registra giros en rotations.tsv y borra las imagenes ya generadas de esas
claves para que el siguiente assemble las vuelva a codificar giradas.

    python rotate_keys.py SNES rotar_snes_derecha.txt 90
    python rotate_keys.py SNES rotar_snes_izquierda.txt -90

Grados en sentido horario: 90 gira a la derecha, -90 a la izquierda. El
fichero lleva una clave por linea; vacias y '#' se ignoran (mismo formato
que exclude_keys.py, y por la misma razon: las claves llevan parentesis,
comillas y '!!').

El giro se aplica al media box-2D (--media para otro). Avisa de las claves
sin ficha y de las que no tienen fuente box-2D en el pool, porque a esas el
giro no les haria nada. Despues hay que REENSAMBLAR el sistema.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

args = [a for a in sys.argv[1:] if not a.startswith('--')]
if len(args) < 3:
    sys.exit(__doc__.strip())
sistema, lista, grados = args[0], args[1], args[2]
try:
    grados = int(grados)
except ValueError:
    sys.exit('grados debe ser un entero: 90, -90, 180')
media = 'box-2D'
for i, a in enumerate(sys.argv):
    if a == '--media' and i + 1 < len(sys.argv):
        media = sys.argv[i + 1]

with open(lista, encoding='utf-8') as f:
    claves = [l.strip() for l in f if l.strip() and not l.startswith('#')]

meta = os.path.join('work', 'meta', sistema)
pool = os.path.join('work', 'pool', media, sistema)
if not os.path.isdir(meta):
    sys.exit('no existe %s' % meta)
en_pool = set()
if os.path.isdir(pool):
    en_pool = {os.path.splitext(n)[0] for n in os.listdir(pool)}

# rotations.tsv: una fila por (media, sistema, clave); una nueva sustituye
tabla = 'rotations.tsv'
filas = {}
if os.path.isfile(tabla):
    with open(tabla, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line or line.startswith('#'):
                continue
            p = line.split('\t')
            if len(p) >= 4:
                filas[(p[0], p[1], p[2])] = p[3]

salidas = [os.path.join('out', d, 'docs', sistema, 'Artwork')
           for d in os.listdir('out') if d.startswith('media-')]
avisos = 0
for clave in claves:
    estado = []
    if not os.path.isfile(os.path.join(meta, clave + '.json')):
        estado.append('SIN FICHA')
        avisos += 1
    elif clave not in en_pool:
        estado.append('sin fuente %s en el pool' % media)
        avisos += 1
    filas[(media, sistema, clave)] = str(grados)
    for art in salidas:
        p = os.path.join(art, clave + '.jpg')
        if os.path.isfile(p):
            os.remove(p)
            estado.append('borrada en ' + art.split(os.sep)[1])
    print('%-40s %s' % (', '.join(estado) or 'registrada', clave))

with open(tabla, 'w', encoding='utf-8', newline='') as f:
    f.write('#media\tsystem\tkey\tdegrees (clockwise; -90 turns left)\n')
    for (m, s, k), d in sorted(filas.items()):
        f.write('%s\t%s\t%s\t%s\n' % (m, s, k, d))

print('\n%d giro(s) de %d grados registrados en %s (%d filas en total)%s. '
      'Reensambla %s.' % (len(claves), grados, tabla, len(filas),
                          '; %d aviso(s)' % avisos if avisos else '', sistema))
