// Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
package main
import ("debug/elf"; "debug/gosym"; "encoding/json"; "fmt"; "os"; "strings")
func main() {
 f,e:=elf.Open(os.Args[1]); if e!=nil {panic(e)}; defer f.Close()
 sec:=f.Section(".gopclntab"); if sec==nil {panic("missing Go PC table")}
 data,e:=sec.Data(); if e!=nil {panic(e)}
 tab,e:=gosym.NewTable(nil,gosym.NewLineTable(data,f.Section(".text").Addr)); if e!=nil {panic(e)}
 found:=[]string{}
 for _,fn:=range tab.Funcs {if strings.Contains(fn.Name,"go-jose") || strings.Contains(fn.Name,"VaultPluginTLSProvider") || strings.Contains(fn.Name,"PluginAPIClientMeta") {found=append(found,fn.Name)}}
 b,_:=json.MarshalIndent(map[string]interface{}{"functions":len(tab.Funcs),"joseAndPluginFunctions":found},"","  ");fmt.Println(string(b))
}
