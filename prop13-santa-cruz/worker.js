import snapshot from './data/data.json' with {type:'json'};
import {createWorker} from './lib/worker.js';
export default createWorker(snapshot);
