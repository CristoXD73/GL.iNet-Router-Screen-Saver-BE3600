/* The download page: works out which computer this is (Windows, Mac or Linux), points the
 * Install and Uninstall buttons at the one right file for it, and says in plain words what to do
 * with it. If the guess is wrong, the small links under the buttons switch to another system. */
(function () {
  'use strict';

  var FILES = {
    windows: { install: 'Install-Screen-Saver.cmd', uninstall: 'Uninstall-Screen-Saver.cmd' },
    mac: { install: 'Install-Screen-Saver-Mac.zip', uninstall: 'Uninstall-Screen-Saver-Mac.zip' },
    linux: { install: 'install-screen-saver.py', uninstall: 'uninstall-screen-saver.py' }
  };
  var NAMES = { windows: 'Windows', mac: 'a Mac', linux: 'Linux' };
  var SHORT = { windows: 'Windows', mac: 'Mac', linux: 'Linux' };

  // What to do with the file, one system at a time. {file} is the file the button gives you.
  var HOW = {
    windows: {
      said: "You're on Windows - click Install, then open the file you downloaded.",
      more: ['If Windows says it protected your PC, click <b>More info</b>, then <b>Run anyway</b>. ' +
             'Your browser may also ask whether to keep the file: choose <b>Keep</b>.']
    },
    mac: {
      said: "You're on a Mac - click Install, then double-click the file in your Downloads folder.",
      more: ['If you see a <b>.zip</b> file, double-click it first; the file to open is inside.',
             'If your Mac says it can’t check the file: open <b>System Settings</b>, then <b>Privacy &amp; Security</b>, ' +
             'scroll down and click <b>Open Anyway</b>.']
    },
    linux: {
      said: "You're on Linux - click Install, then open a terminal and type the line below.",
      more: ['<code>python3 ~/Downloads/{file}</code>']
    }
  };

  function detect() {
    var ua = navigator.userAgent || '';
    var p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
    if (/iPhone|iPad|iPod|Android/i.test(ua)) return 'phone';
    if (/Mac/i.test(p) && navigator.maxTouchPoints > 1) return 'phone';          // an iPad asking for the desktop site
    if (/Win/i.test(p) || /Windows/.test(ua)) return 'windows';
    if (/Mac/i.test(p) || /Macintosh|Mac OS X/.test(ua)) return 'mac';
    if (/Linux|X11|CrOS/i.test(p + ' ' + ua)) return 'linux';
    return 'unknown';
  }

  var $ = function (id) { return document.getElementById(id); };
  var install = $('btnInstall'), uninstall = $('btnUninstall'), said = $('said'), more = $('more'),
      others = $('others'), buttons = $('buttons');
  var action = /uninstall/i.test(location.hash) ? 'uninstall' : 'install';

  function show(os) {
    if (!FILES[os]) {
      buttons.classList.add('hidden');
      said.textContent = os === 'phone'
        ? 'This runs on a computer (Windows, Mac or Linux) that is on your router’s Wi-Fi. Open this page on that computer.'
        : 'Which computer are you on?';
      more.textContent = '';
      renderOthers(null);
      return;
    }
    buttons.classList.remove('hidden');
    install.href = 'downloads/' + FILES[os].install;
    uninstall.href = 'downloads/' + FILES[os].uninstall;
    install.setAttribute('download', FILES[os].install);
    uninstall.setAttribute('download', FILES[os].uninstall);
    install.classList.toggle('primary', action === 'install');
    uninstall.classList.toggle('primary', action === 'uninstall');
    var h = HOW[os], file = FILES[os][action];
    said.textContent = action === 'uninstall' ? h.said.replace('click Install', 'click Uninstall') : h.said;
    more.innerHTML = '';
    h.more.forEach(function (line) {
      var li = document.createElement('li');
      li.innerHTML = line.replace('{file}', file);
      more.appendChild(li);
    });
    document.documentElement.setAttribute('data-os', os);
    renderOthers(os);
  }

  function renderOthers(os) {
    others.textContent = '';
    others.appendChild(document.createTextNode(os ? 'Not on ' + NAMES[os] + '? Get it for ' : 'Get it for '));
    var list = ['windows', 'mac', 'linux'].filter(function (x) { return x !== os; });
    list.forEach(function (x, i) {
      var a = document.createElement('a');
      a.href = '#' + (action === 'uninstall' ? 'uninstall-' : '') + x;
      a.textContent = SHORT[x];
      a.addEventListener('click', function (e) { e.preventDefault(); show(x); });
      if (i) others.appendChild(document.createTextNode(i === list.length - 1 ? ' or ' : ', '));
      others.appendChild(a);
    });
    others.appendChild(document.createTextNode('.'));
  }

  // The buttons also switch which file the words describe (Install or Uninstall).
  [install, uninstall].forEach(function (b) {
    b.addEventListener('click', function () {
      action = b === uninstall ? 'uninstall' : 'install';
      show(document.documentElement.getAttribute('data-os') || detect());
    });
  });

  var forced = /(windows|mac|linux)/.exec(location.hash);
  show(forced ? forced[1] : detect());
  document.documentElement.classList.add('js');
})();
