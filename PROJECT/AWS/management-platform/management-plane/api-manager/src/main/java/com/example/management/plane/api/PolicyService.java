package com.example.management.plane.api;

import com.example.management.agent.GrdcClient;import com.example.management.common.ApiPolicy;import com.example.management.common.contract.AuditLog;import com.example.management.common.contract.DocumentStore;import com.fasterxml.jackson.databind.ObjectMapper;import org.springframework.stereotype.Service;import org.springframework.transaction.annotation.Transactional;import java.util.ArrayList;import java.util.List;import java.util.Map;

@Service
public class PolicyService {
  private final DocumentStore docs;
  private final AuditLog audits;
  private final ObjectMapper json;
  private final GrdcClient grdc;

  public PolicyService(DocumentStore docs, AuditLog audits, ObjectMapper json, GrdcClient client) {
    this.docs = docs;
    this.audits = audits;
    this.json = json;
    this.grdc = client;
  }

  public List<ApiPolicy> list() throws Exception {
    var out = new ArrayList<ApiPolicy>();
    for (var body : docs.list("policy:")) out.add(json.readValue(body, ApiPolicy.class));
    return out;
  }

  @Transactional
  public void save(ApiPolicy p, String actor) throws Exception {
    docs.put("policy:" + p.service(), json.writeValueAsString(p));
    audits.record(actor, "POLICY_SAVE", p.service());
  }

  public Map<String, Object> publish() throws Exception {
    var all = list();
    String body = json.writeValueAsString(all);
    if (!grdc.publish("gateway-policies.json", body, "json"))
      throw new IllegalStateException("Publish failed; saved draft can be republished");
    return Map.of("published", true, "services", all.size());
  }
}
