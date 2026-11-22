"""Full-length login illustration derived from the original blue folded-page mascot."""

FRONT = '''
  <ellipse cx="130" cy="284" rx="92" ry="14" fill="#1D3269" opacity=".27"/>
  <path d="M104 224v43m50-43v43" fill="none" stroke="#233E8B" stroke-width="19" stroke-linecap="round"/>
  <path d="M104 261c-14-2-29 5-29 14 0 8 44 9 44-2v-7zm50 0c14-2 29 5 29 14 0 8-44 9-44-2v-7z" fill="#DCEEFF" stroke="#294697" stroke-width="4"/>
  <path d="M78 177c-23 2-39 16-41 36m143-36c23 2 39 16 41 36" fill="none" stroke="#527ADB" stroke-width="15" stroke-linecap="round"/>
  <path d="M28 215c0-9 5-15 12-14 8 1 11 8 10 15-1 11-9 17-16 13-5-3-6-8-6-14zm204 0c0-9-5-15-12-14-8 1-11 8-10 15 1 11 9 17 16 13 5-3 6-8 6-14z" fill="#DFEEFF" stroke="#2D4BA5" stroke-width="4"/>
  <path d="M82 163q46-22 96 4l-8 56q-34 29-80 0z" fill="#233E8B"/>
  <path d="M80 159q47-22 98 2l-7 55q-36 27-83 3z" fill="url(#vishka-suit)" stroke="#2845A0" stroke-width="5"/>
  <path d="M104 177l13 35 12-21 12 19 15-35" fill="none" stroke="#EDF7FF" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M55 56c1-19 15-31 36-35l72-9 45 33 9 77c2 27-12 45-36 48l-52 6-39 25 8-27c-25 1-40-14-44-33L46 79z" fill="#244093"/>
  <path d="M50 53c3-18 16-32 37-35l71-8 45 34 8 75c3 25-11 43-37 46l-51 6-33 21 6-24c-23 0-40-13-44-34L43 77z" fill="url(#vishka-face)" stroke="#3D63BD" stroke-width="4"/>
  <path d="m158 10 45 34-33 2q-13 0-13-13z" fill="url(#vishka-fold)" stroke="#719BE7" stroke-width="2"/>
  <path d="M59 57q5-18 27-23" fill="none" stroke="#E4F4FF" stroke-width="5" stroke-linecap="round"/>
  <g data-vishka-eyes>
    <ellipse cx="103" cy="87" rx="13.5" ry="19.5" fill="#F8FCFF"/>
    <ellipse cx="159" cy="85" rx="13.5" ry="19.5" fill="#F8FCFF"/>
    <g data-vishka-pupils fill="#152D74">
      <ellipse cx="105" cy="88" rx="6.8" ry="10.5"/>
      <ellipse cx="161" cy="86" rx="6.8" ry="10.5"/>
      <circle cx="103" cy="85" r="2.2" fill="#fff"/>
      <circle cx="159" cy="83" r="2.2" fill="#fff"/>
    </g>
  </g>
  <path d="M115 122q17 16 36-3" fill="none" stroke="#224493" stroke-width="4" stroke-linecap="round"/>
  <path d="M75 112h13m89-2h13" stroke="#D6CBF5" opacity=".78" stroke-width="5" stroke-linecap="round"/>
'''

BACK = '''
  <ellipse cx="130" cy="284" rx="92" ry="14" fill="#1D3269" opacity=".27"/>
  <path d="M104 224v43m50-43v43" fill="none" stroke="#233E8B" stroke-width="19" stroke-linecap="round"/>
  <path d="M104 262c-15-1-24 4-24 13 0 7 38 8 38-1v-8zm50 0c15-1 24 4 24 13 0 7-38 8-38-1v-8z" fill="#B9D3F7" stroke="#294697" stroke-width="4"/>
  <path d="M80 165c-28 6-40 17-44 42m143-42c28 6 40 17 44 42" fill="none" stroke="#527ADB" stroke-width="15" stroke-linecap="round"/>
  <path d="M28 207q-4 17 10 20 11-1 13-17l-5-11zm204 0q4 17-10 20-11-1-13-17l5-11z" fill="#C9DEFA" stroke="#294697" stroke-width="4"/>
  <path d="M80 159q47-22 98 2l-7 55q-36 27-83 3z" fill="url(#vishka-back-suit)" stroke="#2845A0" stroke-width="5"/>
  <path d="M130 172v55" stroke="#89A7F4" stroke-width="3" stroke-dasharray="5 6"/>
  <path d="M50 53c3-18 16-32 37-35l71-8 45 34 8 75c3 25-11 43-37 46l-51 6-33 21 6-24c-23 0-40-13-44-34L43 77z" fill="url(#vishka-back-face)" stroke="#2C499E" stroke-width="4"/>
  <path d="M59 57q5-18 27-23m-21 93q6 21 24 24l40-2" fill="none" stroke="#B4D9FF" opacity=".55" stroke-width="4" stroke-linecap="round"/>
  <path d="M157 10l46 34-30 3q-14-1-16-15z" fill="#B4D5FF" stroke="#638CDD" stroke-width="2"/>
  <path d="M91 75l80-6m-76 25 77-6m-73 25 52-4" stroke="#C4DEFF" opacity=".58" stroke-width="5" stroke-linecap="round"/>
'''


def illustration():
    return (
        '<div class="vishka-login-mascot" data-vishka aria-hidden="true">'
        '<div class="vishka-login-pose">'
        '<svg class="vishka-front" viewBox="0 0 260 300" focusable="false">'
        '<defs><linearGradient id="vishka-face" x2=".65" y2="1">'
        '<stop stop-color="#C9E9FF"/><stop offset=".53" stop-color="#75A6F4"/><stop offset="1" stop-color="#6081DF"/>'
        '</linearGradient><linearGradient id="vishka-fold" x2="1" y2="1">'
        '<stop stop-color="#F0FAFF"/><stop offset="1" stop-color="#8EBDFF"/></linearGradient>'
        '<linearGradient id="vishka-suit" x2="1" y2="1">'
        '<stop stop-color="#7BA9FA"/><stop offset="1" stop-color="#334DB3"/></linearGradient></defs>'
        + FRONT + '</svg><svg class="vishka-back" viewBox="0 0 260 300" focusable="false">'
        '<defs><linearGradient id="vishka-back-face" x2=".8" y2="1">'
        '<stop stop-color="#A7CEFF"/><stop offset="1" stop-color="#5274D3"/></linearGradient>'
        '<linearGradient id="vishka-back-suit" x2="1" y2="1">'
        '<stop stop-color="#6F99ED"/><stop offset="1" stop-color="#354DA5"/></linearGradient></defs>'
        + BACK + '</svg></div></div>'
    )
