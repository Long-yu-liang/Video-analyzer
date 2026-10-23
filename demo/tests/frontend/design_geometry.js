const assert = require('node:assert/strict');
require('../../web/assets/js/gameplay/legacy-design.js');
const api = globalThis.GameplayDesign;
// 16:9 video letterboxed in a square: top and bottom bars are not image coordinates.
const bounds = {left:10, top:20, width:400, height:400};
assert.equal(api.framePoint(100,30,bounds,1600,900), null);
assert.deepEqual(api.framePoint(210,220,bounds,1600,900), {x:.5,y:.5});
assert.deepEqual(api.rectFromPoints({x:.8,y:.9},{x:.1,y:.2}), {x:.1,y:.2,w:.7000000000000001,h:.7});
const d = {control:{}, anchors:[{}], state:{}, status:'accepted'};
api.invalidate([d], d.control);
assert.equal(d.stale,true);assert.equal(d.status,'pending');assert.deepEqual(d.anchors,[]);
assert.throws(()=>api.selections([d]), /重新推荐/);
console.log('Design geometry and stale state passed');
