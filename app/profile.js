// Ajustes desta TV. Outras TVs usam um perfil em profiles/<nome>/ (veja scripts/build.py --profile).
window.MINHATV_PROFILE = {
    city: 'Cidade Ocidental',
    // Bloco de foto da prateleira (fica logo depois da Netflix); null para não ter.
    family: { id: 'minhatv.tuti', name: 'Tuti', tile: 'assets/tuti-tile.jpg', photo: 'assets/tuti.jpg' },
    // Fotos da família ao lado dos destaques. O link do álbum do iCloud fica fora do GitHub:
    // ~/.minhatv/family_album.json, entregue à TV pelo scripts/install.py.
    familyPhotos: { title: 'Família' }
};
