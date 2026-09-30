package com.example.management.routing;
import com.example.management.agent.GrdcClient;import com.alibaba.nacos.api.naming.pojo.Instance;
import org.springframework.context.annotation.*;import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.Scheduled;import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import software.amazon.awssdk.services.elasticloadbalancingv2.ElasticLoadBalancingV2Client;
import software.amazon.awssdk.services.elasticloadbalancingv2.model.*;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;
import java.time.*;import java.net.*;import java.net.http.*;import java.util.*;
import org.slf4j.Logger;import org.slf4j.LoggerFactory;

@Configuration
@EnableConfigurationProperties(RoutingProperties.class)
public class RoutingController {
  private static final Logger log=LoggerFactory.getLogger(RoutingController.class);
  private final RoutingProperties p;private final GrdcClient grdc;private final String holder=UUID.randomUUID().toString();
  private final ElasticLoadBalancingV2Client elb=ElasticLoadBalancingV2Client.builder().overrideConfiguration(c->c.apiCallTimeout(Duration.ofSeconds(8))).build();
  private final DynamoDbClient ddb=DynamoDbClient.builder().overrideConfiguration(c->c.apiCallTimeout(Duration.ofSeconds(5))).build();
  private final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
  private final Map<String,Integer> recoveries=new HashMap<>();
  public RoutingController(RoutingProperties p,GrdcClient g){this.p=p;grdc=g;}
  @Bean SecurityFilterChain security(HttpSecurity h)throws Exception{return h.authorizeHttpRequests(a->a.requestMatchers("/actuator/health/**","/actuator/prometheus").permitAll().anyRequest().denyAll()).build();}
  boolean lease(){
    long now=Instant.now().getEpochSecond();
    try{
      ddb.updateItem(b->b.tableName(p.getLockTable()).key(Map.of("lock_id",AttributeValue.fromS("routing")))
        .updateExpression("SET holder=:h, expires_at=:until")
        .conditionExpression("attribute_not_exists(expires_at) OR expires_at < :now OR holder=:h")
        .expressionAttributeValues(Map.of(":h",AttributeValue.fromS(holder),":now",AttributeValue.fromN(""+now),":until",AttributeValue.fromN(""+(now+30)))));return true;
    }catch(ConditionalCheckFailedException e){return false;}
  }
  @Scheduled(fixedDelayString="${routing.interval-ms:10000}") public void reconcile(){
    try{
      if(!lease())return;
      for(var entry:p.getBindings().entrySet())reconcile(entry.getKey(),entry.getValue());
    }catch(Exception e){log.error("Reconciliation failed; current ALB rules retained",e);}
  }
  void reconcile(String service,RoutingProperties.Binding binding)throws Exception{
    List<Instance> instances=service.equals("management")?List.of():grdc.instances(service);
    Map<String,Boolean> health=new HashMap<>();
    for(var target:binding.targets().entrySet()){
      String site=target.getKey();
      boolean registered=service.equals("management")||instances.stream().anyMatch(i->i.isHealthy()&&i.isEnabled()&&i.getWeight()>0&&site.equals(i.getMetadata().get("site")));
      boolean ready=elb.describeTargetHealth(b->b.targetGroupArn(target.getValue())).targetHealthDescriptions().stream()
        .anyMatch(t->t.targetHealth().state()==TargetHealthStateEnum.HEALTHY);
      boolean probe=service.equals("management")||probe(binding.probes().get(site));
      String key=service+site;int success=registered&&ready&&probe?recoveries.getOrDefault(key,0)+1:0;recoveries.put(key,Math.min(3,success));
      health.put(site,success>=3);
    }
    var weights=WeightDecision.weights(health);
    Action desired;
    if(WeightDecision.unavailable(weights))desired=Action.builder().type(ActionTypeEnum.FIXED_RESPONSE).fixedResponseConfig(FixedResponseActionConfig.builder().statusCode("503").contentType("text/plain").messageBody("No healthy site").build()).build();
    else desired=Action.builder().type(ActionTypeEnum.FORWARD).forwardConfig(ForwardActionConfig.builder().targetGroups(weights.entrySet().stream()
        .map(e->TargetGroupTuple.builder().targetGroupArn(binding.targets().get(e.getKey())).weight(e.getValue()).build()).toList())
      .targetGroupStickinessConfig(TargetGroupStickinessConfig.builder().enabled(false).build()).build()).build();
    var current=elb.describeRules(b->b.ruleArns(binding.ruleArn())).rules().getFirst().actions();

    if(current.size()==1&&sameAction(current.getFirst(),desired))return;
    if(!lease())return;
    elb.modifyRule(b->b.ruleArn(binding.ruleArn()).actions(desired));
    log.info("Updated service={} weights={}",service,weights);
  }
  boolean sameAction(Action current,Action desired){
    if(current.type()!=desired.type())return false;
    if(current.type()==ActionTypeEnum.FIXED_RESPONSE)
      return Objects.equals(current.fixedResponseConfig().statusCode(),desired.fixedResponseConfig().statusCode());
    if(current.forwardConfig()==null)return false;
    var a=new TreeMap<String,Integer>();var b=new TreeMap<String,Integer>();
    current.forwardConfig().targetGroups().forEach(t->a.put(t.targetGroupArn(),t.weight()));
    desired.forwardConfig().targetGroups().forEach(t->b.put(t.targetGroupArn(),t.weight()));
    return a.equals(b);
  }
  boolean probe(String url){
    if(url==null)return false;
    try{return http.send(HttpRequest.newBuilder(URI.create(url+"/actuator/health/readiness")).timeout(Duration.ofSeconds(3)).GET().build(),HttpResponse.BodyHandlers.discarding()).statusCode()==200;}
    catch(Exception e){return false;}
  }
}
