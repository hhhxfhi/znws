import {readFileSync} from "node:fs";
export const name="natural-data-dispatch-unavailable.js";
export async function apply(ctx,config={}) {
 const identityURL=new URL("./../../build-info.json",import.meta.url);
 const info=JSON.parse(readFileSync(identityURL,"utf8"));
 if(!/^payload\/[^/]+-[a-f0-9]{12}$/.test(info.payload))throw Error("Invalid plugin payload identity");
 const implementation=await import(new URL(info.payload+"/"+"unavailable.js",identityURL));
 const child=ctx.plugin(implementation,config);
 await child.await();
}
