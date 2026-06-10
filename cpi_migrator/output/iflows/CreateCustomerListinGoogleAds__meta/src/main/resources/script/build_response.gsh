import com.sap.gateway.ip.core.customdev.util.Message;
import groovy.xml.*;
import groovy.json.*;

def Message build_response_10(Message message) {
    
    def jsonbody = message.getBody(java.lang.String) as String;
    def jsonSlurper = new JsonSlurper();
    def adsResponse = jsonSlurper.parseText(jsonbody);
    def xsiType = message.getProperty("xsiType");
    def nodeListReturnValue = "ListReturnValue.Type" 
   
    def outputBuilder = new StreamingMarkupBuilder()
    outputBuilder.encoding = 'UTF-8';
    def adWordsResponse = outputBuilder.bind {
        mkp.xmlDeclaration()
        //Declare the namespaces
        namespaces << [ nsrm:'https://adwords.google.com/api/adwords/rm/v201506', 
        xsi: 'http://www.w3.org/2001/XMLSchema-instance',
        nscm:"https://adwords.google.com/api/adwords/cm/v201506"]
            nsrm.mutateResponse {
                nsrm.rval {
                    nscm."$nodeListReturnValue"( "UserListReturnValue" ) 
                    nsrm.value('xsi.type': xsiType ){
                        nsrm.id(adsResponse.results[0].resourceName.toString().substring(31,41))
                    }
                }            
            }
        
    }

  message.setBody(XmlUtil.serialize(adWordsResponse).toString());
  return message;
}


def Message build_response_40(Message message) {
    
    def userListId  = message.getProperty("UserListId");

    def outputBuilder = new StreamingMarkupBuilder()
    outputBuilder.encoding = 'UTF-8';
    def adWordsResponse = outputBuilder.bind {
        mkp.xmlDeclaration()
        //Declare the namespaces
        namespaces << [ 
        nsrm:'https://adwords.google.com/api/adwords/rm/v201506', 
        xsi: 'http://www.w3.org/2001/XMLSchema-instance',
        nscm:"https://adwords.google.com/api/adwords/cm/v201506"]
            nsrm.mutateMembersResponse {
                nsrm.rval {
                    nsrm.userLists{
                        nsrm.id( userListId )
                    }
                }            
            }
    }

  message.setBody(XmlUtil.serialize(adWordsResponse).toString());
  return message;
}




def Message build_response_50(Message message) {
    
    def jsonbody = message.getBody(java.lang.String) as String;
    def jsonSlurper = new JsonSlurper();
    def adsResponse = jsonSlurper.parseText(jsonbody);
    
    def nodePageType = "Page.Type";
    def nodeUserListType = "UserList.Type";
    
    def isLogicalUserList = false;
    if ( adsResponse.results[0].userList.logicalUserList ) {
        isLogicalUserList = true;
    }
     
    def outputBuilder = new StreamingMarkupBuilder()
    outputBuilder.encoding = 'UTF-8';
        def adWordsResponse = outputBuilder.bind {
        mkp.xmlDeclaration()
        //Declare the namespaces
        namespaces << [ nsrm:'https://adwords.google.com/api/adwords/rm/v201506', 
        nscm:"https://adwords.google.com/api/adwords/cm/v201506",
        xsi: 'http://www.w3.org/2001/XMLSchema-instance']  
            nsrm.getResponse{
                nsrm.rval{
                    nscm."$nodePageType"( "UserListPage" ) 
                    nsrm.entries('xsi.type': 'ns2:LogicalUserList'){
                        adsResponse.results.each { result ->
                            nsrm.id(result.userList.id)
                            nsrm.name(result.userList.name)
                            nsrm.isReadOnly(result.userList.readOnly)
                            nsrm.listType(result.userList.type)
                            if ( result.userList.type == "LOGICAL" ) {
                               nsrm."$nodeUserListType"( "LogicalUserList" )
                            } else {
                                nsrm."$nodeUserListType"( "CrmBasedUserList" )
                            }
                            if ( isLogicalUserList ) {
                                nsrm.rules{
                                    nsrm.operator("ANY")
                                    result.userList.logicalUserList.rules.each { rule ->
                                            rule.ruleOperands.each { ruleOperand -> 
                                                nsrm.ruleOperands{
                                                    nsrm.UserList('xsi.type': 'ns2:CrmBasedUserList'){
                                                    nsrm.id(ruleOperand.userList.toString().substring(31,41))
                                                    nsrm.listType("CRM_BASED")
                                                     nsrm."$nodeUserListType"( "CrmBasedUserList" )
                                                    }
                                                }
                                        }
                                    } 
                                }
                            }
                        }
                    }
                }
            }
    }
	
    message.setBody(XmlUtil.serialize(adWordsResponse).toString());
    return message;
}


