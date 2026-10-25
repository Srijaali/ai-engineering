// Display only: Kociemba URFDLB indices, no moves, policy, solver or requests.
function cubeSvg(facelets, name='Cube') {
  const faces=[{face:'U',offset:0,origin:[130,10],across:[36,18],down:[-36,18]},
    {face:'F',offset:18,origin:[22,64],across:[36,18],down:[0,36]},
    {face:'R',offset:9,origin:[130,118],across:[36,-18],down:[0,36]}];
  const point=(f,c,r)=>[f.origin[0]+f.across[0]*c+f.down[0]*r,f.origin[1]+f.across[1]*c+f.down[1]*r];
  const tiles=faces.map(f=>Array.from({length:9},(_,i)=>{
    const r=Math.floor(i/3),c=i%3,letter=facelets[f.offset+i],middle=point(f,c+.5,r+.5);
    return `<g data-index="${f.offset+i}" data-face="${f.face}"><polygon points="${[[c,r],[c+1,r],[c+1,r+1],[c,r+1]].map(([x,y])=>point(f,x,y).join(',')).join(' ')}" fill="${colours[letter]||'#FFFFFF'}"/><text x="${middle[0]}" y="${middle[1]}" dominant-baseline="central" text-anchor="middle">${html(letter)}</text></g>`;
  }).join('')).join('');
  return `<svg class="cube-svg" viewBox="0 0 260 240" role="img" aria-label="${html(name)} isometric cube: U top, F front-left, R front-right"><title>${html(name)} · U top / F left / R right</title>${tiles}</svg>`;
}
function cubeDisplay(state,name,index){
  return `<div class="cube-display" data-cube-display><div class="view-toggle" role="group" aria-label="${html(name)} cube view"><button type="button" data-view="cube" aria-pressed="true">Cube</button><button type="button" data-view="net" aria-pressed="false">Net</button></div><div class="cube-visual">${cubeSvg(state,name)}</div><div class="net hidden" role="img" aria-label="${html(name)} unfolded cube">${net(state)}</div></div>`;
}
// Toggling only changes visibility; it cannot request data or change the cube.
document.addEventListener('click',e=>{
  const button=e.target.closest('button[data-view]');if(!button)return;
  const display=button.closest('[data-cube-display]');
  display.querySelector('.cube-visual').classList.toggle('hidden',button.dataset.view!=='cube');
  display.querySelector('.net').classList.toggle('hidden',button.dataset.view!=='net');
  for(const b of display.querySelectorAll('button[data-view]'))b.setAttribute('aria-pressed',String(b===button));
});
