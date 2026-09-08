package com.example.batch;

import org.springframework.util.MultiValueMap;

import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;


public class VerifyProcess {
  static String env(String name, String fallback) {return System.getenv().getOrDefault(name, fallback);}
  static String required(String name){String v=env(name,"");
    if(v.isBlank()) throw new IllegalArgumentException("Missing " + name);
    return v;}
  static String field(Map<String, String> m,String key, String regex) {
    String v=m.getOrDefault(key, "");
    if(!v.matches(regex)) throw new IllegalArgumentException("Invalid " + key);
    return v;}
  static Map<String, String> form(MultiValueMap<String, String> input) {
    Map<String, String> out = new LinkedHashMap<>();
    input.forEach(( String k, List<String> v)->{if(v.size()!=1)throw new IllegalArgumentException("Duplicate field " + k);out.put(k,v.get(0));});
    return out;
  }
  static void validateJob(Map<String, String> m) {
    field(m, "app", "app[12]");field(m, "type", "SH|API");
    field(m, "name", "reconcile");
    String date = field(m, "businessDate", "[0-9]{4}-[0-9]{2}-[0-9]{2}");
    try{LocalDate.parse(date);}catch(Exception e){throw new IllegalArgumentException("Invalid businessDate");}
  }
}
