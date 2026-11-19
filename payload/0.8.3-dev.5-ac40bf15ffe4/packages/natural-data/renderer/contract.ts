export const columnLabelOf=(labels:Record<string,string>|undefined,column:string):string=>labels?.[column]??column;
