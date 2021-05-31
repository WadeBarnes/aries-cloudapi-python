from fastapi import APIRouter, HTTPException
import requests
import json
import os
import logging


from schemas import LedgerRequest, DidCreationResponse, InitWalletRequest

import aries_cloudcontroller


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wallets", tags=["wallets"])

admin_url = os.getenv("ACAPY_ADMIN_URL")
admin_port = os.getenv("ACAPY_ADMIN_PORT")
admin_api_key = os.getenv("ACAPY_ADMIN_API_KEY")
is_multitenant = os.getenv("IS_MULTITENANT", False)
ledger_url = os.getenv("LEDGER_NETWORK_URL")


@router.get(
    "/create-pub-did", tags=["did"], response_model=DidCreationResponse
)
async def create_public_did():
    """
    Create a new public DID and
    write it to the ledger and
    receive its public info.

    Returns:
    * DID object (json)
    * Issuer verkey (str)
    * Issuer Endpoint (url)
    """
    # TODO Can we break down this endpoint into smaller functions?
    # Because this really is too complex/too much happening at once.
    # This way not really testible/robust
    try:
        aries_agent_controller = aries_cloudcontroller.AriesAgentController(
            admin_url=f"{admin_url}:{admin_port}",
            api_key=admin_api_key,
            is_multitenant=is_multitenant,
        )
        # TODO: Should this come from env var or from the client request?
        url = ledger_url
        # Adding empty header as parameters are being sent in payload
        generate_did_res = await aries_agent_controller.wallet.create_did()
        if not generate_did_res["result"]:
            raise HTTPException(
                # TODO: Should this return HTTPException, if so which status code?
                # Check same for occurences below
                status_code=404,
                detail=f"Something went wrong.\nCould not generate DID.\n{generate_did_res}",
            )
        did_object = generate_did_res["result"]
        # TODO: Network and paymentaddr should be definable on the fly/via args/via request body
        # TODO: Should this really be a schema or is using schema overkill here?
        # If we leave it as schema like this I suppose it is at least usable elsewhere
        payload = LedgerRequest(
            network="stagingnet",
            did=did_object["did"],
            verkey=did_object["verkey"],
            paymentaddr="",
        ).dict()
        r = requests.post(url, data=json.dumps(payload), headers={})
        if r.status_code != 200:
            error_json = r.json()
            raise HTTPException(
                status_code=r.status_code,
                detail=f"Something went wrong.\nCould not write to StagingNet.\n{error_json}",
            )
        taa_response = await aries_agent_controller.ledger.get_taa()
        logger.info(f"taa_response:\n{taa_response}")
        if not taa_response["result"]:
            error_json = taa_response.json()
            raise HTTPException(
                status_code=404,
                detail=f"Something went wrong. Could not get TAA. {error_json}",
            )
        TAA = taa_response["result"]["taa_record"]
        TAA["mechanism"] = "service_agreement"
        accept_taa_response = await aries_agent_controller.ledger.accept_taa(TAA)
        logger.info(f"accept_taa_response: {accept_taa_response}")
        if accept_taa_response != {}:
            error_json = accept_taa_response.json()
            raise HTTPException(
                status_code=404,
                detail=f"Something went wrong. Could not accept TAA. {error_json}",
            )
        assign_pub_did_response = await aries_agent_controller.wallet.assign_public_did(
            did_object["did"]
        )
        logger.info(f"assign_pub_did_response:\n{assign_pub_did_response}")
        if (
            not assign_pub_did_response["result"]
            or assign_pub_did_response["result"] == {}
        ):
            error_json = assign_pub_did_response.json()
            raise HTTPException(
                status_code=500,
                detail=f"Something went wrong.\nCould not assign DID. {error_json}",
            )
        get_pub_did_response = await aries_agent_controller.wallet.get_public_did()
        logger.info(f"get_pub_did_response:\n{get_pub_did_response}")
        if not get_pub_did_response["result"] or get_pub_did_response["result"] == {}:
            error_json = get_pub_did_response.json()
            raise HTTPException(
                status_code=404,
                detail=f"Something went wrong. Could not obtain public DID. {error_json}",
            )
        issuer_nym = get_pub_did_response["result"]["did"]
        issuer_verkey = get_pub_did_response["result"]["verkey"]
        issuer_endpoint = await aries_agent_controller.ledger.get_did_endpoint(
            issuer_nym
        )
        if not issuer_endpoint:
            raise HTTPException(
                status_code=404,
                detail="Something went wrong. Could not obtain issuer endpoint.",
            )
        issuer_endpoint_url = issuer_endpoint["endpoint"]
        final_response = DidCreationResponse(
            did_object=did_object,
            issuer_verkey=issuer_verkey,
            issuer_endpoint=issuer_endpoint_url,
        )
        await aries_agent_controller.terminate()
        return final_response
    except Exception as e:
        await aries_agent_controller.terminate()
        logger.error(f"The following error occured:\n{e!r}")
        raise HTTPException(
            status_code=500,
            detail=f"Something went wrong: {e!r}",
        )


@router.get("/")
async def wallets_root():
    """
    The default endpoints for wallets

    TODO: Determine what this should return or
    whether this should return anything at all
    """
    return {
        "message": "Wallets endpoint. Please, visit /docs to consult the Swagger docs."
    }


# TODO: This should be somehow retsricted?!
@router.post("/create-wallet")
async def create_wallet(wallet_payload: InitWalletRequest):
    """
    Create a new wallet

    Parameters:
    -----------
    wallet_payload: dict
    """
    try:
        aries_agent_controller = aries_cloudcontroller.AriesAgentController(
            admin_url=f"{admin_url}:{admin_port}",
            api_key=admin_api_key,
            is_multitenant=is_multitenant,
        )
        if aries_agent_controller.is_multitenant:
            # TODO replace with model for payload/wallet like
            # described https://fastapi.tiangolo.com/tutorial/body/
            # TODO Remove this default wallet. This has to be provided
            # At least unique values for eg label, The rest could be filled
            # with default values like image_url could point to a defautl avatar img
            if not wallet_payload:
                payload = {
                    "image_url": "https://aries.ca/images/sample.png",
                    "key_management_mode": "managed",
                    "label": "Alice",
                    "wallet_dispatch_type": "default",
                    "wallet_key": "MySecretKey1234",
                    "wallet_name": "AlicesWallet",
                    "wallet_type": "indy",
                }
            else:
                payload = wallet_payload
            wallet_response = await aries_agent_controller.multitenant.create_subwallet(
                payload
            )
        else:
            # TODO: Implement wallet_response as schema if that is useful
            wallet_response = await aries_agent_controller.wallet.create_did()
        await aries_agent_controller.terminate()
        return wallet_response
    except Exception as e:
        await aries_agent_controller.terminate()
        raise HTTPException(
            status_code=500,
            detail=f"Something went wrong: {e!r}",
        )


# TODOs see endpoints below
@router.get("/{wallet_id}")
async def get_wallet_info_by_id(wallet_id: str):
    """
    Get the wallet information by id

    Parameters:
    -----------
    wallet_id: str
    """
    pass


@router.get("/{wallet_id}/connections", tags=["connections"])
async def get_connections(wallet_id: str):
    """
    Get all connections for a wallet given the wallet's ID

    Parameters:
    -----------
    wallet_id: str
    """
    pass


@router.get("/{wallet_id}/connections/{conn_id}", tags=["connections"])
async def get_connection_by_id(wallet_id: str, connection_id: str):
    """
    Get the specific connections per wallet per connection
    by respective IDs

    Parameters:
    -----------
    wallet_id: str
    """
    pass


@router.post("/{wallet_id}/connections", tags=["connections"])
async def create_connection_by_id(wallet_id: str):
    """
    Create a connection for a wallet

    Parameters:
    -----------
    wallet_id: str
    """
    pass


@router.put("/{wallet_id}/connections/{conn_id}", tags=["connections"])
async def update_connection_by_id(wallet_id: str, connection_id: str):
    """
    Update a specific connection (by ID) for a
    given wallet (by ID)

    Parameters:
    -----------
    wallet_id: str
    connection_id: str
    """
    pass


@router.delete("/{wallet_id}/connections/{conn_id}", tags=["connections"])
async def delete_connection_by_id(wallet_id: str, connection_id: str):
    """
    Delete a connection (by ID) for a given wallet (by ID)

    Parameters:
    -----------
    wallet_id: str
    connection_id: str
    """
    pass


@router.delete("/{wallet_id}", tags=["connections"])
async def delete_wallet_by_id(wallet_id: str):
    """
    Delete a wallet (by ID)

    Parameters:
    -----------
    wallet_id: str
    """
    # TODO: Should this be admin-only?
    pass


@router.post("/{wallet_id}", tags=["connections"])
async def add_did_to_trusted_reg(wallet_id: str):
    """
    Delete a wallet (by ID)

    Parameters:
    -----------
    wallet_id: str
    """
    # TODO: Should this be admin-only?
    pass


# TODO Add Security and key managements eg. create and exchange new key pairs
