package com.example.management.agent;
import java.util.*;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("platform.agent")
public class AgentProperties {
  public String serverAddr="localhost:8848", username="nacos", password="", namespace="", group="PLATFORM", site="a", service="app1", ip="";
  public int port=8080;
  public boolean register=true;
  public Map<String,String> gateways=new LinkedHashMap<>(Map.of("a","http://gateway-a:8080","b","http://gateway-b:8080"));
  public String getServerAddr(){return serverAddr;} public void setServerAddr(String v){serverAddr=v;}
  public String getUsername(){return username;} public void setUsername(String v){username=v;}
  public String getPassword(){return password;} public void setPassword(String v){password=v;}
  public String getNamespace(){return namespace;} public void setNamespace(String v){namespace=v;}
  public String getGroup(){return group;} public void setGroup(String v){group=v;}
  public String getSite(){return site;} public void setSite(String v){site=v;}
  public String getService(){return service;} public void setService(String v){service=v;}
  public String getIp(){return ip;} public void setIp(String v){ip=v;}
  public int getPort(){return port;} public void setPort(int v){port=v;}
  public boolean isRegister(){return register;} public void setRegister(boolean v){register=v;}
  public Map<String,String> getGateways(){return gateways;} public void setGateways(Map<String,String> v){gateways=v;}
}
