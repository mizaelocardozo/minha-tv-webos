// Ajustes desta TV. Outras TVs usam um perfil em profiles/<nome>/ (veja scripts/build.py --profile).
window.MINHATV_PROFILE = {
    // Nome no topo da tela (o nome na lista de apps da TV fica em app/appinfo.json).
    name: 'Família Siqueira Cardozo',
    city: 'Cidade Ocidental',
    // Bloco de foto da prateleira (fica logo depois da Netflix); null para não ter.
    family: { id: 'minhatv.tuti', name: 'Tuti', tile: 'assets/tuti-tile.jpg', photo: 'assets/tuti.jpg' },
    // Fotos da família ao lado dos destaques. O link do álbum do iCloud fica fora do GitHub:
    // ~/.minhatv/family_album.json, entregue à TV pelo scripts/install.py.
    familyPhotos: { title: 'Família' }
};
