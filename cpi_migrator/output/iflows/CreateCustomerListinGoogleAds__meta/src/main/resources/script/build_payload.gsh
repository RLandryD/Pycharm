import com.sap.gateway.ip.core.customdev.util.Message;
import groovy.xml.XmlUtil;
import groovy.json.*;

//-----------------------------------------------------------------------------
//https://googleads.googleapis.com/v8/customers/{customerId}/userLists:mutate
//-----------------------------------------------------------------------------

def Message build_payload_10(Message message) {
	
    def payload = message.getBody(java.lang.String.class)toString();
    def payloadParsed = new XmlSlurper().parseText( payload );
    JsonBuilder builder = new JsonBuilder();
    
    def targetValidateOnly = message.getProperty("TargetValidateOnly");
    if (targetValidateOnly == null  || targetValidateOnly == '' ) {
            targetValidateOnly = 'false';
    }
    def targetPartialFailure = message.getProperty("TargetPartialFailure");
    if (targetPartialFailure == null  || targetPartialFailure == '' ) {
            targetPartialFailure = 'false';
    }
               
    def userListMethod = payloadParsed.name();
    def userListOperator = payloadParsed.operations[0].operator.toString();;
    def userListType = payloadParsed.operations[0].operand.listType.toString();
      
  //----------------------------------------------------------------------------
  // Create CRM and Logical User List
   //----------------------------------------------------------------------------
    if ( userListMethod.equalsIgnoreCase("mutate") &&
        userListOperator.equalsIgnoreCase("add") ) {
         
        //Create CRM user list
        def userListName = payloadParsed.operations.operand.name.toString();
        def userListDescription = payloadParsed.operations.operand.description.toString();
        def userListIntegrationCode = payloadParsed.operations.operand.integrationCode.toString();
        def userListAccessReason = payloadParsed.operations.operand.accessReason.toString();
        def userListmembershipLifeSpan = payloadParsed.operations.operand.membershipLifeSpan.toString();
         def userSize = payloadParsed.operations.operand.size.toString();
        
       if ( userListType.equalsIgnoreCase("crm_based") ) {
           
            message.setProperty("membershipLifeSpan",userListmembershipLifeSpan);
            int userListmembershipLifeSpanMax = 540;
            if (userListmembershipLifeSpan == null  || userListmembershipLifeSpan == '' || userListmembershipLifeSpan == 0 
                || userListmembershipLifeSpan.toInteger() > userListmembershipLifeSpanMax )
            {
                userListmembershipLifeSpan = userListmembershipLifeSpanMax ;
            }

 
            message.setProperty("xsiType","ns2:CrmBasedUserList");
            
            builder { operations( [ { create {
                                    type userListType
                                    accessReason    userListAccessReason
                                    name            userListName
                                    description     userListDescription
                                    integrationCode userListIntegrationCode
                                    membershipLifeSpan  userListmembershipLifeSpan
                                    crmBasedUserList {
                                        upload_key_type "CONTACT_INFO"
                                        dataSourceType  "FIRST_PARTY" 
                                    } } }
                                  ] )
                        validateOnly targetValidateOnly
                        partialFailure targetPartialFailure
            };
            
            def createCrmUserListRequest = JsonOutput.prettyPrint(builder.toString());
            message.setBody(createCrmUserListRequest);
        
            
        }
    
        if ( userListType.equalsIgnoreCase("logical") ) {
            
            message.setProperty("xsiType","ns2:LogicalUserList");
            
            def crmUserList = [];
            payloadParsed.operations.operand.rules.ruleOperands.UserList.each { p ->
                def userListId = "customers/" + message.getProperty("TargetClientCustomerId") + "/userLists/" + p.id;
                crmUserList.add(userListId);
            }
            
            builder { operations( [ { create {
                                    type userListType
                                    accessReason    userListAccessReason
                                    name            userListName
                                    description     userListDescription
                                    integrationCode userListIntegrationCode
                                    logicalUserList {
                                        rules( [ operator: "ANY",
                                        ruleOperands: crmUserList.collect {[userList: it.toString()]}
                                        ] ) }
                                    } }
                                ] )
                        validateOnly targetValidateOnly
                        partialFailure targetPartialFailure
            };
            def createLogicalUserListRequest = JsonOutput.prettyPrint(builder.toString());
            message.setBody(createLogicalUserListRequest);
        }
  
    }  
    
    
    def addCRMUserListScenario = false;
    if (payloadParsed.operations.operand.rules.size() > 0) {
        addCRMUserListScenario = true;
    }
        

    //----------------------------------------------------------------------------
    // Close CRM User List
    //----------------------------------------------------------------------------
    //if ( userListMethod.equalsIgnoreCase("mutate") &&
        //userListOperator.equalsIgnoreCase("set") && !addCRMUserListScenario ) {
        
        //List crmUserList2CloseList = [];
        //payloadParsed.operations.operand.each { p ->
              //Map crmUserList2Close = [ resourceName: "",
                //                        membershipStatus: "" ]
                //crmUserList2Close.resourceName = "customers/" + message.getProperty("TargetClientCustomerId") + "/userLists/" + p.id;
                //crmUserList2Close.membershipStatus = p.status;
                //crmUserList2CloseList.add(crmUserList2Close);
            //}
            
         //builder ( [ operations: crmUserList2CloseList.collect{element ->
                    //[   updateMask: "membershipStatus",
                        //update: [
                         //resourceName:  element.resourceName.toString(),
                         //membershipStatus: element.membershipStatus.toString()
                        //]
                    //]  }, 
                    //validateOnly: targetValidateOnly,
                    //partialFailure: targetPartialFailure 
                //] );
                
        //def closeCrmUserListRequest = JsonOutput.prettyPrint(builder.toString());
        //message.setBody(closeCrmUserListRequest);  
    //}
    
    
     //----------------------------------------------------------------------------
    // Remove CRM User List
    //----------------------------------------------------------------------------
    if ( userListMethod.equalsIgnoreCase("mutate") &&
        userListOperator.equalsIgnoreCase("set") && !addCRMUserListScenario ) {
        
        List crmUserList2CloseList = [];
        payloadParsed.operations.operand.each { p ->
              Map crmUserList2Close = [ resourceName: "",
                                        membershipStatus: "" ]
                crmUserList2Close.resourceName = "customers/" + message.getProperty("TargetClientCustomerId") + "/userLists/" + p.id;
                crmUserList2Close.membershipStatus = p.status;
                crmUserList2CloseList.add(crmUserList2Close);
            }
            
       builder ( operations: crmUserList2CloseList.collect{element ->
                    [ remove: 
                        element.resourceName.toString() 
                    ] },
                    validateOnly: "false",
                    partialFailure: "false" 
                );
                
        def removedCrmUserListRequest = JsonOutput.prettyPrint(builder.toString());
        message.setBody(removedCrmUserListRequest);  
    }
    
    
    //----------------------------------------------------------------------------
    // Add CRM User List to an existing Logical User List
    //----------------------------------------------------------------------------
    
    if ( userListMethod.equalsIgnoreCase("mutate") &&
        userListOperator.equalsIgnoreCase("set") && addCRMUserListScenario ) {
        
        
        def logicalUserListId = payloadParsed.operations.operand.id.toString();
        def resourceName = "customers/" + message.getProperty("TargetClientCustomerId") + "/userLists/" + logicalUserListId;
         
        def crmUserList = [];
         payloadParsed.operations.operand.rules.ruleOperands.UserList.each { p ->
            def userListId = "customers/" + message.getProperty("TargetClientCustomerId") + "/userLists/" + p.id;
            crmUserList.add(userListId);
        };
     
        builder ( [ operations: 
                    [   updateMask: "logicalUserList.rules",
                        update: [
                         resourceName: resourceName,
                         id:  logicalUserListId,
                         logicalUserList: {
                            rules( [ operator: "ANY",
                                        ruleOperands: crmUserList.collect {[userList: it.toString()]}
                            ] ) }
                         ]
                    ]  , 
                    validateOnly: "false",
                    partialFailure: "false" 
                ] );
                
                
        def addCrmUserListRequest = JsonOutput.prettyPrint(builder.toString());
        message.setBody(addCrmUserListRequest );  
    }
    
	return message;
}


//----------------------------------------------------------------------------
//https://googleads.googleapis.com/v8/customers/{customerId}/offlineUserDataJobs:create
//----------------------------------------------------------------------------
def Message build_payload_20(Message message) {
	
    def payload = message.getBody(java.lang.String.class)toString();
    def payloadParsed = new XmlSlurper().parseText( payload );
    JsonBuilder builder = new JsonBuilder();
    
    def userListId = payloadParsed.operations[0].operand.userListId.toString();
    message.setProperty("UserListId",userListId);
      
    //----------------------------------------------------------------------------
    // Create OfflineJob
    //----------------------------------------------------------------------------
    def userListResource = "customers/" + message.getProperty("TargetClientCustomerId") + 
                           "/userLists/" + userListId ;
   
    def targetValidateOnly = message.getProperty("TargetValidateOnly");
    if (targetValidateOnly == null  || targetValidateOnly == '' ) {
            targetValidateOnly = 'false';
    }

    builder  {  job {
                    type     "CUSTOMER_MATCH_USER_LIST"
                     customerMatchUserListMetadata {
                        userList  userListResource 
                        consent {
                            adUserData "GRANTED"
                            adPersonalization "GRANTED"
                        }
                     }
                }
             validateOnly   targetValidateOnly   
    }
             
            
    
    def createOfflineJobRequest = JsonOutput.prettyPrint(builder.toString());
    message.setBody(createOfflineJobRequest);  
    
	return message;
}


//----------------------------------------------------------------------------
//https://googleads.googleapis.com/v8/{resourceName=customers/*/offlineUserDataJobs/*}:addOperations
//----------------------------------------------------------------------------
def Message build_payload_30(Message message) {
	
    def payload = message.getBody(java.lang.String.class)toString();
    def payloadParsed = new XmlSlurper().parseText( payload );
    JsonBuilder builder = new JsonBuilder();
    
    def idOrigin = "hashedEmail";
    switch ( payloadParsed.operations.operand.dataType) {
             case "PHONE_SHA256":
               idOrigin = "hashedPhoneNumber";
                break;
    }
    
    List userListMember2AddList = [];
    payloadParsed.operations.operand.members.each { p ->
        Map userListMember2Add = [
            idOrigin: "",
            id: ""
        ]
        userListMember2Add.id = p.toString();
        userListMember2Add.idOrigin = idOrigin;
        userListMember2AddList.add(userListMember2Add);
     }
    
    
    def targetValidateOnly = message.getProperty("TargetValidateOnly");
    if (targetValidateOnly == null  || targetValidateOnly == '' ) {
            targetValidateOnly = 'false';
    }
    def targetPartialFailure = message.getProperty("TargetPartialFailure");
    if (targetPartialFailure == null  || targetPartialFailure == '' ) {
            targetPartialFailure = 'false';
    }
      
    builder ( operations: 
            userListMember2AddList.collect{element ->
                    [ create: 
                        [ userIdentifiers: [
                            [ "$element.idOrigin": element.id.toString() ]
                        ]]
                    ]
            },
        
        validateOnly: targetValidateOnly,
        enablePartialFailure: targetPartialFailure
    );
       
    def OfflineUserDataJobOperationRequest = JsonOutput.prettyPrint(builder.toString());
    message.setBody(OfflineUserDataJobOperationRequest);  
        
	return message;
}



//----------------------------------------------------------------------------
//https://googleads.googleapis.com/v8/{resourceName=customers/*/offlineUserDataJobs/*}:run
//----------------------------------------------------------------------------
def Message build_payload_40(Message message) {
	
    //----------------------------------------------------------------------------
    // Run OfflineJob
    //----------------------------------------------------------------------------
    def targetValidateOnly = message.getProperty("TargetValidateOnly");
    if (targetValidateOnly == null  || targetValidateOnly == '' ) {
            targetValidateOnly = 'false';
    }

    JsonBuilder builder = new JsonBuilder();
    builder  {  validateOnly   targetValidateOnly }
    
    def runOfflineJobRequest = JsonOutput.prettyPrint(builder.toString());
    message.setBody(runOfflineJobRequest);  
    
    
	return message;
}


//----------------------------------------------------------------------------
//Get Open user lists
//----------------------------------------------------------------------------
def Message build_payload_50(Message message) {
	
    def payload = message.getBody(java.lang.String.class)toString();
    def payloadParsed = new XmlSlurper().parseText( payload );
    JsonBuilder builder = new JsonBuilder();
    
    //Predicates
    def whereClause = "";
    def predicates = "user_list.membership_status = " + "\'" + "OPEN" + "\'";
    payloadParsed.serviceSelector.predicates.each { p ->
            switch ( p.field ) {
                case "Id":
                    whereClause = "user_list.id = " + "\'" + p.values + "\'";
                    break;
                    
            }
            
            if ( whereClause?.trim() ) {
                predicates = predicates + " and " + whereClause;
            }
        }
    

    //Requested Fields
    def requestedFields = "user_list.id, user_list.name, user_list.description, user_list.type, user_list.read_only, user_list.logical_user_list.rules";   
   
    //Buid API request Body
   def query_str = "SELECT " +   requestedFields + 
                    " FROM user_list" +
                    " WHERE " + predicates;
    
    builder { query         query_str };
    
    def getUserListRequest = JsonOutput.prettyPrint(builder.toString());
    message.setBody(getUserListRequest);
    
    
    
	return message;
}