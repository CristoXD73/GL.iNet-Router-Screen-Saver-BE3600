# Open-source references and runtime

The Screen Studio contains project-specific browser code plus behavior/design inspiration and runtime dependencies from permissively licensed open-source projects.

## Runtime used by Atmosphere

### Three.js

- Version pinned by this package: r134
- https://threejs.org/
- License: MIT

### Vanta.js

- Version pinned by this package: 0.5.24
- https://github.com/tengbao/vanta
- License: MIT

The package references the pinned CDN builds from the HTML file. They power the six Atmosphere scenes.

## Eye-system references

### fonzu/RobotEye

- https://github.com/fonzu/RobotEye
- License: MIT

Used as reference for vector expression/state ideas and autonomous eye behavior.

### tanmaywankar/Grobot_Animations

- https://github.com/tanmaywankar/Grobot_Animations
- License: MIT

Used as reference for the physics-flavored **Spring** style/behavior model.

### sachinthra/robo_eyes

- https://github.com/sachinthra/robo_eyes
- License: MIT

Used as reference for pupil/lid/emotion behavior in the **Lidded** style.

## Project-specific code

The BE3600 SVG browser adapter, editor integration, 284×76 capture pipeline, RGB565 conversion, framebuffer rotation, frame deduplication and BEA1 compiler in `Screen-Studio.html` are the project-specific implementation assembled for this BE3600 workflow.

When publishing or redistributing the project, retain appropriate upstream license notices for the third-party projects you include or redistribute directly.
